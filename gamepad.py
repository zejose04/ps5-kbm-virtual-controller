"""Camada de emulação do controle virtual PlayStation (thread-safe, ~100 Hz).

Responsabilidades:
    1. Verificar disponibilidade real do driver ViGEmBus via Windows SetupAPI
       antes de tentar importar ou conectar o `vgamepad`.
    2. Isolar o backend de hardware virtual (`VDS4ViGEmBackend`) atrás de uma
       interface abstrata (`GamepadBackend`), permitindo testes determinísticos
       sem driver (`SimulatedDS4Backend`) e futura troca de backend (ex.: VIIPER).
    3. Manter o estado completo do controle de forma thread-safe (`RLock`),
       substituindo integralmente o estado anterior a cada quadro enviado.
    4. Executar um loop estável de atualização (~100 Hz) com baixo uso de CPU.
    5. Garantir encerramento seguro (reset de botões, D-Pad, analógicos e
       gatilhos, parada limpa da thread e desconexão do dispositivo virtual).
"""

from __future__ import annotations

import atexit
from abc import ABC, abstractmethod
from collections import deque
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import gc
import importlib.util
import platform
import threading
import time
from typing import Any

from config import (
    AnalogAxis,
    ControllerConfig,
    DEFAULT_CONFIG,
    DPadDirection,
    DS4_PRODUCT_ID,
    DS4_VENDOR_ID,
    PSButton,
    TriggerName,
    VIGEMBUS_INTERFACE_GUID,
    VIGEMBUS_RECOMMENDED_VERSION,
    VIGEMBUS_RELEASE_URL,
    VIGEMBUS_SERVICE_NAME,
    VIGEMBUS_SYS_PATH,
)


# ---------------------------------------------------------------------------
# Exceções de domínio
# ---------------------------------------------------------------------------

class VirtualGamepadError(RuntimeError):
    """Erro base da camada de controle virtual."""


class DriverUnavailableError(VirtualGamepadError):
    """Levantada quando o driver ViGEmBus não está ativo/funcional no Windows."""


class BackendDependencyError(VirtualGamepadError):
    """Levantada quando a dependência Python (ex.: vgamepad) não está instalada."""


# ---------------------------------------------------------------------------
# Mapeamento fiel do protocolo HID DualShock 4 (9 bytes base)
# ---------------------------------------------------------------------------

# Bits de wButtons conforme ViGEm / DualShock 4 HID Report
DS4_BUTTON_BITMASK: dict[PSButton, int] = {
    PSButton.SQUARE: 1 << 4,
    PSButton.CROSS: 1 << 5,
    PSButton.CIRCLE: 1 << 6,
    PSButton.TRIANGLE: 1 << 7,
    PSButton.L1: 1 << 8,
    PSButton.R1: 1 << 9,
    PSButton.L2: 1 << 10,
    PSButton.R2: 1 << 11,
    PSButton.SHARE: 1 << 12,
    PSButton.OPTIONS: 1 << 13,
    PSButton.L3: 1 << 14,
    PSButton.R3: 1 << 15,
}

# Bits de bSpecial conforme ViGEm / DualShock 4 HID Report
DS4_SPECIAL_BITMASK: dict[PSButton, int] = {
    PSButton.PS: 1 << 0,
    PSButton.TOUCHPAD: 1 << 1,
}

# Valores do nibble inferior (bits 0..3) de wButtons para o D-Pad (HAT)
DS4_DPAD_CODE: dict[DPadDirection, int] = {
    DPadDirection.UP: 0x0,
    DPadDirection.UP_RIGHT: 0x1,
    DPadDirection.RIGHT: 0x2,
    DPadDirection.DOWN_RIGHT: 0x3,
    DPadDirection.DOWN: 0x4,
    DPadDirection.DOWN_LEFT: 0x5,
    DPadDirection.LEFT: 0x6,
    DPadDirection.UP_LEFT: 0x7,
    DPadDirection.NONE: 0x8,
}


def clamp_float(value: float, low: float, high: float) -> float:
    """Limita um valor float ao intervalo fechado [low, high]."""
    return max(low, min(high, float(value)))


def axis_float_to_ds4_byte(value: float) -> int:
    """Converte eixo normalizado [-1.0, +1.0] para byte HID DS4 [0, 255].

    Segue exatamente a fórmula do `vgamepad >= 0.1.0`:
        -1.0 -> 1, 0.0 -> 128 (centro), +1.0 -> 255.
    """
    clamped = clamp_float(value, -1.0, 1.0)
    return max(0, min(255, 128 + round(clamped * 127)))


def trigger_float_to_ds4_byte(value: float) -> int:
    """Converte gatilho normalizado [0.0, 1.0] para byte HID DS4 [0, 255]."""
    clamped = clamp_float(value, 0.0, 1.0)
    return max(0, min(255, round(clamped * 255)))


# ---------------------------------------------------------------------------
# Estruturas de estado e métricas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DS4RawReport:
    """Representação dos 9 bytes de entrada do relatório HID DualShock 4."""

    thumb_lx: int = 128
    thumb_ly: int = 128
    thumb_rx: int = 128
    thumb_ry: int = 128
    buttons_word: int = 0x0008  # Bits 0..3 = 0x8 (D-Pad neutro), bits 4..15 = 0
    special_byte: int = 0x00
    trigger_l: int = 0
    trigger_r: int = 0

    def is_neutral(self) -> bool:
        """Retorna True se todos os comandos estiverem completamente neutros."""
        return (
            self.thumb_lx == 128
            and self.thumb_ly == 128
            and self.thumb_rx == 128
            and self.thumb_ry == 128
            and self.buttons_word == 0x0008
            and self.special_byte == 0x00
            and self.trigger_l == 0
            and self.trigger_r == 0
        )


@dataclass
class ControllerState:
    """Estado lógico completo do controle virtual PlayStation."""

    buttons: dict[PSButton, bool] = field(
        default_factory=lambda: {btn: False for btn in PSButton}
    )
    dpad: DPadDirection = DPadDirection.NONE
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    l2: float = 0.0
    r2: float = 0.0

    def clone(self) -> ControllerState:
        """Cria uma cópia independente do estado para uso seguro entre threads."""
        return ControllerState(
            buttons=dict(self.buttons),
            dpad=self.dpad,
            lx=self.lx,
            ly=self.ly,
            rx=self.rx,
            ry=self.ry,
            l2=self.l2,
            r2=self.r2,
        )

    def reset(self) -> None:
        """Zera todos os botões, centraliza os eixos e solta os gatilhos."""
        for btn in PSButton:
            self.buttons[btn] = False
        self.dpad = DPadDirection.NONE
        self.lx = 0.0
        self.ly = 0.0
        self.rx = 0.0
        self.ry = 0.0
        self.l2 = 0.0
        self.r2 = 0.0

    def is_neutral(self) -> bool:
        """Retorna True se nenhum comando estiver ativo."""
        return (
            not any(self.buttons.values())
            and self.dpad == DPadDirection.NONE
            and self.lx == 0.0
            and self.ly == 0.0
            and self.rx == 0.0
            and self.ry == 0.0
            and self.l2 == 0.0
            and self.r2 == 0.0
        )

    def to_ds4_raw_report(self, config: ControllerConfig = DEFAULT_CONFIG) -> DS4RawReport:
        """Converte o estado lógico atual nos campos exatos do relatório DS4."""
        effective_ly = -self.ly if config.invert_y_axis else self.ly
        effective_ry = -self.ry if config.invert_y_axis else self.ry

        w_buttons = DS4_DPAD_CODE[self.dpad]
        for btn, bit in DS4_BUTTON_BITMASK.items():
            if self.buttons.get(btn, False):
                w_buttons |= bit

        # Mantém bits digitais L2/R2 coerentes com os gatilhos analógicos (como no DS4 real)
        if self.l2 >= config.trigger_digital_threshold:
            w_buttons |= DS4_BUTTON_BITMASK[PSButton.L2]
        if self.r2 >= config.trigger_digital_threshold:
            w_buttons |= DS4_BUTTON_BITMASK[PSButton.R2]

        b_special = 0
        for btn, bit in DS4_SPECIAL_BITMASK.items():
            if self.buttons.get(btn, False):
                b_special |= bit

        return DS4RawReport(
            thumb_lx=axis_float_to_ds4_byte(self.lx),
            thumb_ly=axis_float_to_ds4_byte(effective_ly),
            thumb_rx=axis_float_to_ds4_byte(self.rx),
            thumb_ry=axis_float_to_ds4_byte(effective_ry),
            buttons_word=w_buttons,
            special_byte=b_special,
            trigger_l=trigger_float_to_ds4_byte(self.l2),
            trigger_r=trigger_float_to_ds4_byte(self.r2),
        )


@dataclass(frozen=True)
class LoopMetrics:
    """Métricas de telemetria do loop de atualização."""

    running: bool
    target_hz: float
    actual_hz: float
    frames_sent: int
    state_revision: int
    uptime_seconds: float
    last_error: str | None


@dataclass(frozen=True)
class ViGEmProbeResult:
    """Resultado da sonda não-invasiva do driver ViGEmBus no Windows."""

    is_windows: bool
    sys_file_exists: bool
    interface_present: bool
    interface_win32_error: int
    vgamepad_installed: bool
    ready_for_virtual_device: bool
    summary: str


# ---------------------------------------------------------------------------
# Sonda SetupAPI (somente leitura) para o barramento ViGEmBus
# ---------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


def _parse_guid(guid_str: str) -> _GUID:
    clean = guid_str.strip().strip("{}")
    parts = clean.split("-")
    data1 = int(parts[0], 16)
    data2 = int(parts[1], 16)
    data3 = int(parts[2], 16)
    tail = bytes.fromhex(parts[3] + parts[4])
    return _GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*tail))


def check_device_interface_present(guid_str: str, present_only: bool = True) -> tuple[bool, int]:
    """Consulta a Windows SetupAPI para verificar se existe interface PnP ativa.

    Operação estritamente somente leitura; não carrega drivers nem exige admin.
    """
    if platform.system() != "Windows":
        return False, -1

    DIGCF_PRESENT = 0x00000002
    DIGCF_DEVICEINTERFACE = 0x00000010
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    flags = DIGCF_DEVICEINTERFACE | (DIGCF_PRESENT if present_only else 0)
    guid = _parse_guid(guid_str)

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    ]
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p

    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    handle = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, flags)
    if not handle or handle == INVALID_HANDLE_VALUE:
        return False, ctypes.get_last_error()

    try:
        iface = _SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
        ok = bool(
            setupapi.SetupDiEnumDeviceInterfaces(
                handle, None, ctypes.byref(guid), 0, ctypes.byref(iface)
            )
        )
        err = 0 if ok else ctypes.get_last_error()
        return ok, err
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)


def probe_vigembus_driver() -> ViGEmProbeResult:
    """Verifica sem efeitos colaterais se o ViGEmBus e o `vgamepad` estão prontos.

    Importante:
        Nunca importa `vgamepad`, pois `vgamepad.win.virtual_gamepad` executa
        `vigem_connect()` no momento do import. Usa `importlib.util.find_spec`
        para detectar se o pacote está instalado e SetupAPI para validar a
        interface real do barramento virtual.
    """
    is_win = platform.system() == "Windows"
    sys_exists = VIGEMBUS_SYS_PATH.exists() if is_win else False
    vgamepad_installed = importlib.util.find_spec("vgamepad") is not None

    if not is_win:
        return ViGEmProbeResult(
            is_windows=False,
            sys_file_exists=False,
            interface_present=False,
            interface_win32_error=-1,
            vgamepad_installed=vgamepad_installed,
            ready_for_virtual_device=False,
            summary="Sistema operacional não é Windows; backend ViGEmBus indisponível.",
        )

    iface_present, win32_err = check_device_interface_present(
        VIGEMBUS_INTERFACE_GUID, present_only=True
    )

    if iface_present and vgamepad_installed:
        summary = "ViGEmBus ativo (interface PnP presente) e pacote vgamepad instalado."
        ready = True
    elif iface_present and not vgamepad_installed:
        summary = (
            "Interface PnP do ViGEmBus está ativa, mas o pacote Python 'vgamepad' "
            "ainda não está instalado neste ambiente."
        )
        ready = False
    elif not iface_present and sys_exists:
        summary = (
            f"Binário ViGEmBus.sys encontrado, porém nenhuma interface PnP ativa "
            f"foi localizada via SetupAPI (Win32 error={win32_err}). "
            f"Driver incompleto/órfão; reinstale o ViGEmBus {VIGEMBUS_RECOMMENDED_VERSION}."
        )
        ready = False
    else:
        summary = (
            f"Driver ViGEmBus não detectado via SetupAPI (Win32 error={win32_err}). "
            f"Instale o ViGEmBus {VIGEMBUS_RECOMMENDED_VERSION}."
        )
        ready = False

    return ViGEmProbeResult(
        is_windows=True,
        sys_file_exists=sys_exists,
        interface_present=iface_present,
        interface_win32_error=win32_err,
        vgamepad_installed=vgamepad_installed,
        ready_for_virtual_device=ready,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Interface de Backend e implementações (ViGEmBus DS4 + Simulador)
# ---------------------------------------------------------------------------

class GamepadBackend(ABC):
    """Contrato abstrato de backend de emulação de controle PlayStation."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Nome legível do backend."""

    @property
    @abstractmethod
    def is_virtual_hardware(self) -> bool:
        """Indica se o backend cria um dispositivo real visível ao Windows."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Indica se o dispositivo virtual está conectado no momento."""

    @abstractmethod
    def connect(self) -> None:
        """Inicializa e conecta o dispositivo virtual."""

    @abstractmethod
    def apply_state(self, state: ControllerState, config: ControllerConfig) -> None:
        """Substitui integralmente o estado do dispositivo pelo snapshot fornecido."""

    @abstractmethod
    def disconnect(self) -> None:
        """Libera e desconecta o dispositivo virtual."""

    @abstractmethod
    def get_device_info(self) -> dict[str, Any]:
        """Retorna metadados técnicos do dispositivo conectado."""


class VDS4ViGEmBackend(GamepadBackend):
    """Backend real baseado em `vgamepad.VDS4Gamepad()` sobre ViGEmBus.

    Importa `vgamepad` de forma preguiçosa (lazy) exclusivamente em `connect()`,
    somente após confirmar que a interface PnP do ViGEmBus está presente.
    """

    def __init__(self) -> None:
        self._vg: Any = None
        self._gamepad: Any = None
        self._connected: bool = False
        self._vid: int | None = None
        self._pid: int | None = None
        self._index: int | None = None

    @property
    def backend_name(self) -> str:
        return "ViGEmBus + vgamepad.VDS4Gamepad (DualShock 4 USB HID)"

    @property
    def is_virtual_hardware(self) -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected and self._gamepad is not None

    def connect(self) -> None:
        if self._connected:
            return

        probe = probe_vigembus_driver()
        if not probe.is_windows:
            raise DriverUnavailableError(probe.summary)

        if not probe.interface_present:
            raise DriverUnavailableError(
                f"{probe.summary}\n"
                f"Download oficial do ViGEmBus {VIGEMBUS_RECOMMENDED_VERSION}: "
                f"{VIGEMBUS_RELEASE_URL}"
            )

        if not probe.vgamepad_installed:
            raise BackendDependencyError(
                "Pacote 'vgamepad' não está instalado no ambiente Python atual.\n"
                "Instale com segurança (sem disparar instalador legado embutido):\n"
                '  $env:VGAMEPAD_SKIP_VIGEMBUS_INSTALL = "true"\n'
                "  pip install -r requirements.txt"
            )

        try:
            import vgamepad as vg  # noqa: PLC0415 — lazy import proposital
        except Exception as exc:
            raise DriverUnavailableError(
                f"Falha ao conectar ao barramento ViGEmBus durante import de vgamepad: {exc}"
            ) from exc

        try:
            gamepad = vg.VDS4Gamepad()
        except Exception as exc:
            raise DriverUnavailableError(
                f"Falha ao criar dispositivo virtual VDS4Gamepad no ViGEmBus: {exc}"
            ) from exc

        self._vg = vg
        self._gamepad = gamepad
        self._connected = True
        try:
            self._vid = int(gamepad.get_vid())
            self._pid = int(gamepad.get_pid())
            self._index = int(gamepad.get_index())
        except Exception:
            self._vid = DS4_VENDOR_ID
            self._pid = DS4_PRODUCT_ID
            self._index = None

    def apply_state(self, state: ControllerState, config: ControllerConfig) -> None:
        if not self.is_connected:
            raise VirtualGamepadError("Dispositivo virtual VDS4Gamepad não está conectado.")

        vg = self._vg
        gp = self._gamepad
        raw = state.to_ds4_raw_report(config)

        # Substituição integral: parte sempre de relatório limpo para evitar bits residuais
        gp.reset()

        # Botões digitais padrão (wButtons bits 4..15)
        for btn, bit in DS4_BUTTON_BITMASK.items():
            if raw.buttons_word & bit:
                gp.press_button(button=vg.DS4_BUTTONS(bit))

        # Botões especiais (PS e Clique do Touchpad)
        for btn, bit in DS4_SPECIAL_BITMASK.items():
            if raw.special_byte & bit:
                gp.press_special_button(special_button=vg.DS4_SPECIAL_BUTTONS(bit))

        # Opcionalmente envia coordenada central de toque junto ao clique do touchpad
        if (
            config.touchpad_click_with_center_finger
            and hasattr(gp, "touchpad")
        ):
            if raw.special_byte & DS4_SPECIAL_BITMASK[PSButton.TOUCHPAD]:
                gp.touchpad(config.touchpad_center_xy, None)
            else:
                gp.touchpad(None, None)

        # D-Pad (HAT 0..8)
        dpad_val = raw.buttons_word & 0x000F
        gp.directional_pad(direction=vg.DS4_DPAD_DIRECTIONS(dpad_val))

        # Analógicos (bytes exatos 0..255 já respeitando inversão configurada)
        gp.left_joystick(x_value=raw.thumb_lx, y_value=raw.thumb_ly)
        gp.right_joystick(x_value=raw.thumb_rx, y_value=raw.thumb_ry)

        # Gatilhos analógicos independentes (0..255)
        gp.left_trigger(value=raw.trigger_l)
        gp.right_trigger(value=raw.trigger_r)

        gp.update()

    def disconnect(self) -> None:
        if self._gamepad is not None:
            try:
                self._gamepad.reset()
                self._gamepad.update()
            except Exception:
                pass
            self._gamepad = None
            # Força coleta imediata para acionar __del__ -> vigem_target_remove()
            gc.collect()
        self._connected = False
        self._index = None

    def get_device_info(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "virtual_hardware": True,
            "connected": self.is_connected,
            "vid": f"0x{self._vid:04X}" if self._vid is not None else "N/A",
            "pid": f"0x{self._pid:04X}" if self._pid is not None else "N/A",
            "vigem_index": self._index,
        }


class SimulatedDS4Backend(GamepadBackend):
    """Backend em memória (sem driver) para validação offline da arquitetura.

    Registra todos os relatórios DS4 gerados sem criar dispositivo no Windows.
    Útil para validar lógica de estado, conversão HID, thread-safety, loop de
    100 Hz e encerramento seguro antes da instalação do ViGEmBus.
    """

    def __init__(self, history_limit: int = 256) -> None:
        self._connected: bool = False
        self._lock = threading.Lock()
        self._last_state: ControllerState = ControllerState()
        self._last_report: DS4RawReport = DS4RawReport()
        self._applied_frames: int = 0
        self._history: deque[DS4RawReport] = deque(maxlen=history_limit)

    @property
    def backend_name(self) -> str:
        return "Simulador DS4 em memória (SEM driver ViGEmBus)"

    @property
    def is_virtual_hardware(self) -> bool:
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        with self._lock:
            self._connected = True
            self._last_state = ControllerState()
            self._last_report = DS4RawReport()

    def apply_state(self, state: ControllerState, config: ControllerConfig) -> None:
        if not self._connected:
            raise VirtualGamepadError("Backend simulado não está conectado.")
        raw = state.to_ds4_raw_report(config)
        with self._lock:
            self._last_state = state.clone()
            self._last_report = raw
            self._applied_frames += 1
            self._history.append(raw)

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def get_last_report(self) -> DS4RawReport:
        with self._lock:
            return self._last_report

    def get_history(self) -> list[DS4RawReport]:
        with self._lock:
            return list(self._history)

    def get_device_info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": self.backend_name,
                "virtual_hardware": False,
                "connected": self._connected,
                "vid": f"0x{DS4_VENDOR_ID:04X} (simulado)",
                "pid": f"0x{DS4_PRODUCT_ID:04X} (simulado)",
                "frames_applied": self._applied_frames,
            }


# ---------------------------------------------------------------------------
# Temporizador de alta resolução no Windows (sem privilégios administrativos)
# ---------------------------------------------------------------------------

class _WindowsHighResTimer:
    """Solicita resolução de timer de 1 ms via winmm.dll durante o loop ativo."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled and platform.system() == "Windows"
        self._active = False
        self._winmm: Any = None

    def __enter__(self) -> _WindowsHighResTimer:
        if self._enabled:
            try:
                self._winmm = ctypes.WinDLL("winmm")
                if self._winmm.timeBeginPeriod(1) == 0:
                    self._active = True
            except Exception:
                self._active = False
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._active and self._winmm is not None:
            try:
                self._winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._active = False


# ---------------------------------------------------------------------------
# Controlador principal thread-safe com loop de ~100 Hz e encerramento seguro
# ---------------------------------------------------------------------------

class VirtualPlayStationController:
    """Gerencia o ciclo de vida, estado thread-safe e loop ~100 Hz do controle."""

    def __init__(
        self,
        backend: GamepadBackend | None = None,
        config: ControllerConfig = DEFAULT_CONFIG,
        auto_loop: bool = True,
    ) -> None:
        self.config = config
        self.backend: GamepadBackend = backend if backend is not None else VDS4ViGEmBackend()
        self._auto_loop = auto_loop

        self._lock = threading.RLock()
        self._state = ControllerState()
        self._state_revision: int = 0

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._frames_sent: int = 0
        self._actual_hz: float = 0.0
        self._last_error: str | None = None
        self._closed: bool = False
        self._atexit_registered: bool = False
        self._push_timestamps: deque[float] = deque(maxlen=60)

    # -- Propriedades de estado ---------------------------------------------

    @property
    def is_running(self) -> bool:
        with self._lock:
            if not self._auto_loop:
                return (not self._closed) and self.backend.is_connected
            return (
                self._thread is not None
                and self._thread.is_alive()
                and self.backend.is_connected
            )

    @property
    def is_virtual_hardware(self) -> bool:
        return self.backend.is_virtual_hardware

    # -- Ciclo de vida ------------------------------------------------------

    def start(self) -> None:
        """Conecta o backend, envia estado neutro inicial e inicia o loop (se auto_loop)."""
        with self._lock:
            if self.is_running:
                return
            self._closed = False
            self._stop_event.clear()
            self._state.reset()
            self._state_revision += 1
            self._frames_sent = 0
            self._actual_hz = 0.0
            self._last_error = None
            self._push_timestamps.clear()

            self.backend.connect()
            try:
                self.backend.apply_state(self._state.clone(), self.config)
            except Exception:
                self.backend.disconnect()
                raise

            self._started_at = time.perf_counter()
            if self._auto_loop:
                self._thread = threading.Thread(
                    target=self._update_loop,
                    name="PSVirtualControllerLoop",
                    daemon=False,
                )
                self._thread.start()

            if not self._atexit_registered:
                atexit.register(self._atexit_cleanup)
                self._atexit_registered = True

    def close(self) -> None:
        """Encerra com segurança: zera entradas, para thread e libera dispositivo."""
        with self._lock:
            if self._closed and not self.backend.is_connected:
                return
            self._closed = True
            self._state.reset()
            self._state_revision += 1
            thread = self._thread
            self._stop_event.set()

        # Aguarda encerramento limpo da thread fora do lock para evitar deadlock
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.config.thread_join_timeout_s)

        with self._lock:
            self._thread = None
            # Envia quadros neutros síncronos garantindo que nada fique pressionado
            if self.backend.is_connected:
                neutral = ControllerState()
                for _ in range(max(1, self.config.shutdown_reset_frames)):
                    try:
                        self.backend.apply_state(neutral, self.config)
                    except Exception:
                        break
                    time.sleep(self.config.shutdown_reset_interval_s)
                try:
                    self.backend.disconnect()
                except Exception:
                    pass

            if self._atexit_registered:
                try:
                    atexit.unregister(self._atexit_cleanup)
                except Exception:
                    pass
                self._atexit_registered = False

    def _atexit_cleanup(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> VirtualPlayStationController:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # -- Loop estável de atualização (~100 Hz) ------------------------------

    def _update_loop(self) -> None:
        target_hz = self.config.validated_hz()
        period = 1.0 / target_hz
        timestamps: deque[float] = deque(maxlen=max(10, int(target_hz // 2)))

        with _WindowsHighResTimer(self.config.use_high_res_timer_windows):
            next_tick = time.perf_counter()
            while not self._stop_event.is_set():
                now = time.perf_counter()
                with self._lock:
                    snapshot = self._state.clone()
                    connected = self.backend.is_connected

                if not connected:
                    break

                try:
                    self.backend.apply_state(snapshot, self.config)
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                    break

                tick_done = time.perf_counter()
                timestamps.append(tick_done)
                actual_hz = 0.0
                if len(timestamps) >= 2:
                    elapsed = timestamps[-1] - timestamps[0]
                    if elapsed > 0:
                        actual_hz = (len(timestamps) - 1) / elapsed

                with self._lock:
                    self._frames_sent += 1
                    self._actual_hz = actual_hz

                next_tick += period
                remaining = next_tick - time.perf_counter()
                if remaining > 0:
                    # Dorme eficientemente até próximo quadro, acordando instantaneamente no stop
                    self._stop_event.wait(timeout=remaining)
                elif remaining < -(period * 3.0):
                    # Evita rajada de compensação caso o SO tenha atrasado a thread
                    next_tick = time.perf_counter()

    # -- API pública thread-safe de controle de entradas --------------------

    def reset_all(self) -> None:
        """Solta todos os botões, centraliza os analógicos e zera os gatilhos."""
        with self._lock:
            self._state.reset()
            self._state_revision += 1

    def set_full_state(self, new_state: ControllerState) -> None:
        """Substitui atomicamente todo o estado do controle em uma única operação.

        Garante que não existam quadros intermediários ("torn frames") ao
        alternar contextos (ex.: transição instantânea A Pé <-> Veículo).
        """
        cloned = new_state.clone()
        with self._lock:
            self._state = cloned
            self._state_revision += 1

    def push_state_now(self, new_state: ControllerState) -> None:
        """Substitui o estado e envia IMEDIATAMENTE ao ViGEmBus (zero double-buffering).

        Usado pelo motor de baixa latência (~200 Hz) para eliminar o atraso de
        esperar uma segunda thread acordar antes de enviar o relatório HID.
        """
        cloned = new_state.clone()
        with self._lock:
            self._state = cloned
            self._state_revision += 1
            if not self.backend.is_connected:
                return
            self.backend.apply_state(cloned, self.config)
            now = time.perf_counter()
            self._frames_sent += 1
            self._push_timestamps.append(now)
            if len(self._push_timestamps) >= 2:
                elapsed = self._push_timestamps[-1] - self._push_timestamps[0]
                if elapsed > 0:
                    self._actual_hz = (len(self._push_timestamps) - 1) / elapsed

    def set_button(self, button: PSButton, pressed: bool) -> None:
        """Define o estado de um botão PlayStation.

        Para L2 e R2, sincroniza também o gatilho analógico correspondente
        (1.0 quando pressionado como botão, 0.0 quando solto).
        """
        pressed_bool = bool(pressed)
        with self._lock:
            self._state.buttons[button] = pressed_bool
            if button == PSButton.L2:
                self._state.l2 = 1.0 if pressed_bool else 0.0
            elif button == PSButton.R2:
                self._state.r2 = 1.0 if pressed_bool else 0.0
            self._state_revision += 1

    def press_button(self, button: PSButton) -> None:
        self.set_button(button, True)

    def release_button(self, button: PSButton) -> None:
        self.set_button(button, False)

    def set_dpad(self, direction: DPadDirection) -> None:
        """Define a direção do D-Pad (substitui imediatamente a anterior)."""
        with self._lock:
            self._state.dpad = direction
            self._state_revision += 1

    def set_left_stick(self, x: float, y: float) -> None:
        """Define LX e LY simultaneamente no intervalo [-1.0, +1.0]."""
        with self._lock:
            self._state.lx = clamp_float(x, -1.0, 1.0)
            self._state.ly = clamp_float(y, -1.0, 1.0)
            self._state_revision += 1

    def set_right_stick(self, x: float, y: float) -> None:
        """Define RX e RY simultaneamente no intervalo [-1.0, +1.0]."""
        with self._lock:
            self._state.rx = clamp_float(x, -1.0, 1.0)
            self._state.ry = clamp_float(y, -1.0, 1.0)
            self._state_revision += 1

    def set_axis(self, axis: AnalogAxis, value: float) -> None:
        """Define individualmente um eixo analógico (LX, LY, RX ou RY)."""
        clamped = clamp_float(value, -1.0, 1.0)
        with self._lock:
            if axis == AnalogAxis.LX:
                self._state.lx = clamped
            elif axis == AnalogAxis.LY:
                self._state.ly = clamped
            elif axis == AnalogAxis.RX:
                self._state.rx = clamped
            elif axis == AnalogAxis.RY:
                self._state.ry = clamped
            self._state_revision += 1

    def set_trigger(self, trigger: TriggerName, value: float) -> None:
        """Define valor analógico contínuo [0.0, 1.0] de L2 ou R2."""
        clamped = clamp_float(value, 0.0, 1.0)
        digital_on = clamped >= self.config.trigger_digital_threshold
        with self._lock:
            if trigger == TriggerName.L2:
                self._state.l2 = clamped
                self._state.buttons[PSButton.L2] = digital_on
            elif trigger == TriggerName.R2:
                self._state.r2 = clamped
                self._state.buttons[PSButton.R2] = digital_on
            self._state_revision += 1

    # -- Telemetria e inspeção ----------------------------------------------

    def get_state(self) -> ControllerState:
        """Retorna cópia consistente (thread-safe) do estado atual."""
        with self._lock:
            return self._state.clone()

    def get_raw_report(self) -> DS4RawReport:
        """Retorna os bytes HID DS4 equivalentes ao estado lógico atual."""
        return self.get_state().to_ds4_raw_report(self.config)

    def get_metrics(self) -> LoopMetrics:
        """Retorna métricas de frequência e saúde do loop de atualização."""
        with self._lock:
            uptime = (
                (time.perf_counter() - self._started_at)
                if self._started_at is not None and self.is_running
                else 0.0
            )
            return LoopMetrics(
                running=self.is_running,
                target_hz=self.config.validated_hz(),
                actual_hz=self._actual_hz,
                frames_sent=self._frames_sent,
                state_revision=self._state_revision,
                uptime_seconds=uptime,
                last_error=self._last_error,
            )

    def format_telemetry(self) -> str:
        """Formata painel de telemetria contínua com os valores enviados."""
        st = self.get_state()
        raw = st.to_ds4_raw_report(self.config)
        metrics = self.get_metrics()

        def flag(btn: PSButton) -> str:
            return "ON " if st.buttons.get(btn, False) else "OFF"

        mode_tag = (
            "REAL (ViGEmBus DS4)"
            if self.is_virtual_hardware
            else "SIMULAÇÃO (sem driver ViGEmBus)"
        )
        status_tag = "ATIVO" if metrics.running else "PARADO"

        lines = [
            f"MODO: {mode_tag} | LOOP: {status_tag} "
            f"({metrics.actual_hz:5.1f} Hz / alvo {metrics.target_hz:.0f} Hz | "
            f"quadros: {metrics.frames_sent})",
            (
                f"BOTÕES: Cross: {flag(PSButton.CROSS)} "
                f"Circle: {flag(PSButton.CIRCLE)} "
                f"Square: {flag(PSButton.SQUARE)} "
                f"Triangle: {flag(PSButton.TRIANGLE)} "
                f"L1: {flag(PSButton.L1)} "
                f"R1: {flag(PSButton.R1)}"
            ),
            (
                f"        L2: {flag(PSButton.L2)} "
                f"R2: {flag(PSButton.R2)} "
                f"L3: {flag(PSButton.L3)} "
                f"R3: {flag(PSButton.R3)} "
                f"Options: {flag(PSButton.OPTIONS)} "
                f"Share: {flag(PSButton.SHARE)} "
                f"Touchpad: {flag(PSButton.TOUCHPAD)} "
                f"PS: {flag(PSButton.PS)}"
            ),
            f"D-PAD:  {st.dpad.value}",
            f"ANALÓGICO ESQUERDO: LX: {st.lx:+.2f}  LY: {st.ly:+.2f}   "
            f"(HID: LX={raw.thumb_lx:3d}, LY={raw.thumb_ly:3d})",
            f"ANALÓGICO DIREITO:  RX: {st.rx:+.2f}  RY: {st.ry:+.2f}   "
            f"(HID: RX={raw.thumb_rx:3d}, RY={raw.thumb_ry:3d})",
            f"GATILHOS: L2: {st.l2:.2f} (HID={raw.trigger_l:3d})   "
            f"R2: {st.r2:.2f} (HID={raw.trigger_r:3d})",
        ]
        if metrics.last_error:
            lines.append(f"ULTIMO ERRO DO LOOP: {metrics.last_error}")
        return "\n".join(lines)
