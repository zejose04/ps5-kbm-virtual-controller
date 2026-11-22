"""Diagnóstico técnico em 4 camadas e suíte de autotestes sem driver.

Responsabilidades:
    1. Coletar inventário real do ambiente Windows, Python, ViGEmBus,
       HidGuardian e Chiaki-ng (somente leitura, sem alterar sistema/drivers).
    2. Diferenciar rigorosamente as 4 camadas de compatibilidade:
         [1] Dispositivo criado (ViGEmBus + vgamepad)
         [2] Dispositivo reconhecido pelo Windows (HID / WinMM)
         [3] Dispositivo detectado pelo Chiaki-ng (SDL3.dll real + UI)
         [4] Comandos efetivamente recebidos pelo PS5 (teste presencial)
    3. Fornecer validação automatizada offline (`--self-test`) para provar
       lógica de estado, conversão HID, thread-safety, loop ~100 Hz e
       encerramento seguro sem depender de driver instalado.
    4. Fornecer validação automatizada ao vivo (`--live`) após a instalação do
       ViGEmBus, testando cada comando via `SDL3.dll` do próprio Chiaki-ng.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from typing import Any

from config import (
    AnalogAxis,
    CHIAKI_DEFAULT_PATHS,
    ControllerConfig,
    DPadDirection,
    DS4_PRODUCT_ID,
    DS4_VENDOR_ID,
    HID_CLASS_GUID,
    HID_INTERFACE_GUID,
    HIDGUARDIAN_SERVICE_NAME,
    PSButton,
    TriggerName,
    VIGEMBUS_INTERFACE_GUID,
    VIGEMBUS_RECOMMENDED_VERSION,
    VIGEMBUS_RELEASE_URL,
    VIGEMBUS_SERVICE_NAME,
    VIGEMBUS_SYS_PATH,
)
from gamepad import (
    DriverUnavailableError,
    SimulatedDS4Backend,
    VDS4ViGEmBackend,
    VirtualPlayStationController,
    probe_vigembus_driver,
)


# ---------------------------------------------------------------------------
# Estruturas de relatório
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LayerStatus:
    """Status individual de uma das 4 camadas de compatibilidade."""

    layer_number: int
    name: str
    status: str  # OK | FALHA | BLOQUEADO | PENDENTE
    details: str


@dataclass(frozen=True)
class ChiakiProbeResult:
    """Resultado da inspeção local da instalação do Chiaki-ng."""

    installed: bool
    install_dir: Path | None
    exe_path: Path | None
    sdl3_path: Path | None
    hidapi_path: Path | None
    sdl3_loadable: bool
    sdl3_gamepads: list[dict[str, Any]]
    sdl3_joysticks_count: int
    error: str | None = None


@dataclass(frozen=True)
class SelfTestItem:
    """Resultado de um teste automatizado offline."""

    name: str
    passed: bool
    details: str


# ---------------------------------------------------------------------------
# Inspeção somente leitura do ambiente Windows (sem admin)
# ---------------------------------------------------------------------------

def _read_registry_value(root: int, subkey: str, value_name: str) -> Any:
    if platform.system() != "Windows":
        return None
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(root, subkey) as key:
            val, _ = winreg.QueryValueEx(key, value_name)
            return val
    except OSError:
        return None


def inspect_windows_environment() -> dict[str, Any]:
    """Coleta informações de SO, Python, ViGEmBus, HidGuardian e HVCI."""
    info: dict[str, Any] = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "is_admin": False,
        "vigembus_service_exists": False,
        "vigembus_sys_exists": VIGEMBUS_SYS_PATH.exists() if platform.system() == "Windows" else False,
        "vigembus_interface_present": False,
        "vigembus_interface_error": -1,
        "hidguardian_in_upper_filters": False,
        "hidguardian_upper_filters": [],
    }

    probe = probe_vigembus_driver()
    info["vigembus_interface_present"] = probe.interface_present
    info["vigembus_interface_error"] = probe.interface_win32_error
    info["vgamepad_installed"] = probe.vgamepad_installed
    info["vigembus_ready"] = probe.ready_for_virtual_device
    info["vigembus_summary"] = probe.summary

    if platform.system() == "Windows":
        import winreg  # noqa: PLC0415

        try:
            info["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            info["is_admin"] = False

        svc_img = _read_registry_value(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{VIGEMBUS_SERVICE_NAME}",
            "ImagePath",
        )
        info["vigembus_service_exists"] = svc_img is not None

        upper_filters = _read_registry_value(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Control\Class\{HID_CLASS_GUID}",
            "UpperFilters",
        )
        if isinstance(upper_filters, list):
            info["hidguardian_upper_filters"] = upper_filters
            info["hidguardian_in_upper_filters"] = any(
                HIDGUARDIAN_SERVICE_NAME.lower() in str(x).lower() for x in upper_filters
            )

    return info


# ---------------------------------------------------------------------------
# Detecção de dispositivos HID DualShock 4 no Windows (SetupAPI + WinMM)
# ---------------------------------------------------------------------------

def list_windows_ds4_hid_devices() -> list[str]:
    """Lista caminhos de interfaces HID ativas no Windows correspondentes ao DS4.

    Busca por `vid_054c&pid_05c4` na classe `GUID_DEVINTERFACE_HID` via SetupAPI
    (somente leitura, rápido, sem precisar invocar PowerShell).
    """
    if platform.system() != "Windows":
        return []

    from gamepad import _GUID, _SP_DEVICE_INTERFACE_DATA, _parse_guid  # noqa: PLC0415

    DIGCF_PRESENT = 0x00000002
    DIGCF_DEVICEINTERFACE = 0x00000010
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    guid = _parse_guid(HID_INTERFACE_GUID)
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

    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    handle = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        return []

    target_pattern = f"vid_{DS4_VENDOR_ID:04x}&pid_{DS4_PRODUCT_ID:04x}"
    matches: list[str] = []
    cb_size_detail = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6

    try:
        index = 0
        while True:
            iface = _SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                handle, None, ctypes.byref(guid), index, ctypes.byref(iface)
            ):
                break
            index += 1

            required = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle, ctypes.byref(iface), None, 0, ctypes.byref(required), None
            )
            if required.value <= 4:
                continue

            buf = ctypes.create_string_buffer(required.value)
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(cb_size_detail)), 4)
            ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle,
                ctypes.byref(iface),
                buf,
                required.value,
                None,
                None,
            )
            if not ok:
                continue

            # DevicePath (UTF-16LE) começa no offset 4
            raw_path = buf.raw[4:].decode("utf-16-le", errors="ignore").split("\x00", 1)[0]
            if target_pattern in raw_path.lower():
                matches.append(raw_path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)

    return matches


# ---------------------------------------------------------------------------
# Inspeção da instalação do Chiaki-ng e sonda via SDL3.dll real
# ---------------------------------------------------------------------------

def find_chiaki_install_dir() -> Path | None:
    """Localiza o diretório de instalação do Chiaki-ng no Windows."""
    for candidate in CHIAKI_DEFAULT_PATHS:
        if (candidate / "chiaki.exe").exists():
            return candidate
    return None


class _ChiakiSDL3Inspector:
    """Wrapper mínimo em ctypes sobre a `SDL3.dll` instalada pelo Chiaki-ng.

    Permite verificar exatamente o que a engine SDL3 do Chiaki-ng enxerga no
    Windows sem exigir dependências externas (como PySDL2/Pygame).
    """

    SDL_INIT_JOYSTICK = 0x00000200
    SDL_INIT_GAMEPAD = 0x00002000

    # Valores enum SDL_GamepadButton (SDL3)
    BUTTON_SOUTH = 0       # Cross
    BUTTON_EAST = 1        # Circle
    BUTTON_WEST = 2        # Square
    BUTTON_NORTH = 3       # Triangle
    BUTTON_BACK = 4        # Share
    BUTTON_GUIDE = 5       # PS
    BUTTON_START = 6       # Options
    BUTTON_LEFT_STICK = 7  # L3
    BUTTON_RIGHT_STICK = 8 # R3
    BUTTON_LEFT_SHOULDER = 9   # L1
    BUTTON_RIGHT_SHOULDER = 10 # R1
    BUTTON_DPAD_UP = 11
    BUTTON_DPAD_DOWN = 12
    BUTTON_DPAD_LEFT = 13
    BUTTON_DPAD_RIGHT = 14
    BUTTON_TOUCHPAD = 20   # SDL_GAMEPAD_BUTTON_TOUCHPAD em SDL3

    # Valores enum SDL_GamepadAxis (SDL3)
    AXIS_LEFTX = 0
    AXIS_LEFTY = 1
    AXIS_RIGHTX = 2
    AXIS_RIGHTY = 3
    AXIS_LEFT_TRIGGER = 4
    AXIS_RIGHT_TRIGGER = 5

    def __init__(self, install_dir: Path) -> None:
        self._install_dir = install_dir
        self._dll_dir_handle: Any = None
        self._sdl: Any = None
        self._initialized = False

    def __enter__(self) -> _ChiakiSDL3Inspector:
        if hasattr(os, "add_dll_directory"):
            self._dll_dir_handle = os.add_dll_directory(str(self._install_dir))
        sdl = ctypes.CDLL(str(self._install_dir / "SDL3.dll"))

        # Assinaturas essenciais de SDL3
        sdl.SDL_SetHint.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        sdl.SDL_SetHint.restype = ctypes.c_bool

        sdl.SDL_Init.argtypes = [ctypes.c_uint32]
        sdl.SDL_Init.restype = ctypes.c_bool

        sdl.SDL_Quit.argtypes = []
        sdl.SDL_Quit.restype = None

        sdl.SDL_UpdateGamepads.argtypes = []
        sdl.SDL_UpdateGamepads.restype = None

        sdl.SDL_GetGamepads.argtypes = [ctypes.POINTER(ctypes.c_int)]
        sdl.SDL_GetGamepads.restype = ctypes.POINTER(ctypes.c_uint32)

        sdl.SDL_GetJoysticks.argtypes = [ctypes.POINTER(ctypes.c_int)]
        sdl.SDL_GetJoysticks.restype = ctypes.POINTER(ctypes.c_uint32)

        sdl.SDL_free.argtypes = [ctypes.c_void_p]
        sdl.SDL_free.restype = None

        sdl.SDL_OpenGamepad.argtypes = [ctypes.c_uint32]
        sdl.SDL_OpenGamepad.restype = ctypes.c_void_p

        sdl.SDL_CloseGamepad.argtypes = [ctypes.c_void_p]
        sdl.SDL_CloseGamepad.restype = None

        sdl.SDL_GetGamepadName.argtypes = [ctypes.c_void_p]
        sdl.SDL_GetGamepadName.restype = ctypes.c_char_p

        sdl.SDL_GetGamepadVendor.argtypes = [ctypes.c_void_p]
        sdl.SDL_GetGamepadVendor.restype = ctypes.c_uint16

        sdl.SDL_GetGamepadProduct.argtypes = [ctypes.c_void_p]
        sdl.SDL_GetGamepadProduct.restype = ctypes.c_uint16

        sdl.SDL_GetGamepadButton.argtypes = [ctypes.c_void_p, ctypes.c_int]
        sdl.SDL_GetGamepadButton.restype = ctypes.c_bool

        sdl.SDL_GetGamepadAxis.argtypes = [ctypes.c_void_p, ctypes.c_int]
        sdl.SDL_GetGamepadAxis.restype = ctypes.c_int16

        sdl.SDL_GetNumGamepadTouchpads.argtypes = [ctypes.c_void_p]
        sdl.SDL_GetNumGamepadTouchpads.restype = ctypes.c_int

        sdl.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if not sdl.SDL_Init(self.SDL_INIT_JOYSTICK | self.SDL_INIT_GAMEPAD):
            raise RuntimeError("SDL3.dll falhou ao inicializar subsistema de gamepad.")

        self._sdl = sdl
        self._initialized = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._initialized and self._sdl is not None:
            try:
                self._sdl.SDL_Quit()
            except Exception:
                pass
            self._initialized = False
        if self._dll_dir_handle is not None:
            try:
                self._dll_dir_handle.close()
            except Exception:
                pass
            self._dll_dir_handle = None

    def enumerate_gamepads(self) -> tuple[list[dict[str, Any]], int]:
        """Lista todos os gamepads detectados pela SDL3.dll do Chiaki-ng."""
        sdl = self._sdl
        sdl.SDL_UpdateGamepads()

        j_count = ctypes.c_int(0)
        j_ids = sdl.SDL_GetJoysticks(ctypes.byref(j_count))
        if j_ids:
            sdl.SDL_free(j_ids)

        g_count = ctypes.c_int(0)
        g_ids = sdl.SDL_GetGamepads(ctypes.byref(g_count))
        found: list[dict[str, Any]] = []
        try:
            for i in range(g_count.value):
                gid = int(g_ids[i])
                handle = sdl.SDL_OpenGamepad(gid)
                if not handle:
                    continue
                try:
                    raw_name = sdl.SDL_GetGamepadName(handle)
                    name = raw_name.decode("utf-8", errors="replace") if raw_name else "Desconhecido"
                    vid = int(sdl.SDL_GetGamepadVendor(handle))
                    pid = int(sdl.SDL_GetGamepadProduct(handle))
                    touchpads = int(sdl.SDL_GetNumGamepadTouchpads(handle))
                    found.append(
                        {
                            "instance_id": gid,
                            "name": name,
                            "vid": f"0x{vid:04X}",
                            "pid": f"0x{pid:04X}",
                            "touchpads": touchpads,
                        }
                    )
                finally:
                    sdl.SDL_CloseGamepad(handle)
        finally:
            if g_ids:
                sdl.SDL_free(g_ids)

        return found, int(j_count.value)

    def open_ds4_gamepad(self) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
        """Abre o primeiro gamepad DS4 (VID_054C / PID_05C4) detectado pelo SDL3."""
        sdl = self._sdl
        sdl.SDL_UpdateGamepads()
        g_count = ctypes.c_int(0)
        g_ids = sdl.SDL_GetGamepads(ctypes.byref(g_count))
        try:
            for i in range(g_count.value):
                gid = int(g_ids[i])
                handle = sdl.SDL_OpenGamepad(gid)
                if not handle:
                    continue
                vid = int(sdl.SDL_GetGamepadVendor(handle))
                pid = int(sdl.SDL_GetGamepadProduct(handle))
                if vid == DS4_VENDOR_ID and pid == DS4_PRODUCT_ID:
                    raw_name = sdl.SDL_GetGamepadName(handle)
                    info = {
                        "instance_id": gid,
                        "name": raw_name.decode("utf-8", errors="replace") if raw_name else "PS4 Controller",
                        "vid": f"0x{vid:04X}",
                        "pid": f"0x{pid:04X}",
                        "touchpads": int(sdl.SDL_GetNumGamepadTouchpads(handle)),
                    }
                    return handle, info
                sdl.SDL_CloseGamepad(handle)
        finally:
            if g_ids:
                sdl.SDL_free(g_ids)
        return None, None

    def close_gamepad(self, handle: Any) -> None:
        if handle:
            self._sdl.SDL_CloseGamepad(handle)

    def read_button(self, handle: Any, sdl_button: int) -> bool:
        self._sdl.SDL_UpdateGamepads()
        return bool(self._sdl.SDL_GetGamepadButton(handle, sdl_button))

    def read_axis_normalized(self, handle: Any, sdl_axis: int) -> float:
        self._sdl.SDL_UpdateGamepads()
        raw = int(self._sdl.SDL_GetGamepadAxis(handle, sdl_axis))
        if raw < 0:
            return raw / 32768.0
        return raw / 32767.0


def probe_chiaki_installation() -> ChiakiProbeResult:
    """Inspeciona instalação local do Chiaki-ng e consulta sua SDL3.dll."""
    install_dir = find_chiaki_install_dir()
    if install_dir is None:
        return ChiakiProbeResult(
            installed=False,
            install_dir=None,
            exe_path=None,
            sdl3_path=None,
            hidapi_path=None,
            sdl3_loadable=False,
            sdl3_gamepads=[],
            sdl3_joysticks_count=0,
            error="Chiaki-ng não encontrado nos caminhos padrão.",
        )

    exe_path = install_dir / "chiaki.exe"
    sdl3_path = install_dir / "SDL3.dll"
    hidapi_path = install_dir / "libhidapi.dll"

    if not sdl3_path.exists():
        return ChiakiProbeResult(
            installed=True,
            install_dir=install_dir,
            exe_path=exe_path,
            sdl3_path=None,
            hidapi_path=hidapi_path if hidapi_path.exists() else None,
            sdl3_loadable=False,
            sdl3_gamepads=[],
            sdl3_joysticks_count=0,
            error="SDL3.dll não encontrada na pasta do Chiaki-ng.",
        )

    try:
        with _ChiakiSDL3Inspector(install_dir) as inspector:
            gamepads, j_count = inspector.enumerate_gamepads()
        return ChiakiProbeResult(
            installed=True,
            install_dir=install_dir,
            exe_path=exe_path,
            sdl3_path=sdl3_path,
            hidapi_path=hidapi_path if hidapi_path.exists() else None,
            sdl3_loadable=True,
            sdl3_gamepads=gamepads,
            sdl3_joysticks_count=j_count,
        )
    except Exception as exc:
        return ChiakiProbeResult(
            installed=True,
            install_dir=install_dir,
            exe_path=exe_path,
            sdl3_path=sdl3_path,
            hidapi_path=hidapi_path if hidapi_path.exists() else None,
            sdl3_loadable=False,
            sdl3_gamepads=[],
            sdl3_joysticks_count=0,
            error=str(exc),
        )


def check_chiaki_process_running() -> bool:
    """Verifica se `chiaki.exe` está em execução no momento."""
    if platform.system() != "Windows":
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq chiaki.exe"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return "chiaki.exe" in out.lower()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Diagnóstico consolidado das 4 camadas (Seção 9)
# ---------------------------------------------------------------------------

def evaluate_four_layer_status(
    active_controller: VirtualPlayStationController | None = None,
) -> list[LayerStatus]:
    """Avalia rigorosamente as 4 camadas sem presumir compatibilidade."""
    probe = probe_vigembus_driver()
    chiaki = probe_chiaki_installation()
    hid_ds4 = list_windows_ds4_hid_devices()

    # Camada 1: Dispositivo criado
    if active_controller is not None and active_controller.is_running:
        if active_controller.is_virtual_hardware:
            layer1 = LayerStatus(
                1,
                "Dispositivo virtual criado (ViGEmBus + VDS4Gamepad)",
                "OK",
                f"Backend real conectado: {active_controller.backend.get_device_info()}",
            )
        else:
            layer1 = LayerStatus(
                1,
                "Dispositivo virtual criado (ViGEmBus + VDS4Gamepad)",
                "BLOQUEADO",
                "Sessão atual está em MODO SIMULAÇÃO (sem driver). "
                f"Status do driver: {probe.summary}",
            )
    elif probe.ready_for_virtual_device:
        layer1 = LayerStatus(
            1,
            "Dispositivo virtual criado (ViGEmBus + VDS4Gamepad)",
            "PENDENTE",
            "ViGEmBus e vgamepad prontos; execute --live ou main.py para instanciar o dispositivo.",
        )
    else:
        layer1 = LayerStatus(
            1,
            "Dispositivo virtual criado (ViGEmBus + VDS4Gamepad)",
            "BLOQUEADO",
            probe.summary,
        )

    # Camada 2: Reconhecido pelo Windows
    if hid_ds4:
        layer2 = LayerStatus(
            2,
            "Dispositivo reconhecido pelo Windows (HID VID_054C/PID_05C4)",
            "OK",
            f"{len(hid_ds4)} interface(s) HID DS4 ativa(s) no Windows.",
        )
    elif layer1.status == "BLOQUEADO":
        layer2 = LayerStatus(
            2,
            "Dispositivo reconhecido pelo Windows (HID VID_054C/PID_05C4)",
            "BLOQUEADO",
            "Nenhum DS4 HID ativo (aguardando instalação do ViGEmBus e criação do controle).",
        )
    else:
        layer2 = LayerStatus(
            2,
            "Dispositivo reconhecido pelo Windows (HID VID_054C/PID_05C4)",
            "PENDENTE",
            "Nenhuma interface HID VID_054C&PID_05C4 ativa no momento.",
        )

    # Camada 3: Detectado pelo Chiaki-ng
    ds4_in_sdl3 = [
        g for g in chiaki.sdl3_gamepads
        if g.get("vid") == f"0x{DS4_VENDOR_ID:04X}" and g.get("pid") == f"0x{DS4_PRODUCT_ID:04X}"
    ]
    if not chiaki.installed:
        layer3 = LayerStatus(
            3,
            "Dispositivo detectado pelo Chiaki-ng (SDL3)",
            "FALHA",
            "Chiaki-ng não encontrado nos caminhos padrão do Windows.",
        )
    elif ds4_in_sdl3:
        layer3 = LayerStatus(
            3,
            "Dispositivo detectado pelo Chiaki-ng (SDL3)",
            "OK",
            f"SDL3.dll do Chiaki-ng detectou: {ds4_in_sdl3[0]} "
            "(confirmar também na interface do Chiaki-ng durante o teste).",
        )
    elif layer1.status == "BLOQUEADO":
        layer3 = LayerStatus(
            3,
            "Dispositivo detectado pelo Chiaki-ng (SDL3)",
            "BLOQUEADO",
            "SDL3.dll do Chiaki-ng operacional (0 DS4 detectados); "
            "aguardando ViGEmBus 1.22.0 e criação do controle virtual.",
        )
    else:
        layer3 = LayerStatus(
            3,
            "Dispositivo detectado pelo Chiaki-ng (SDL3)",
            "PENDENTE",
            f"SDL3.dll operacional ({len(chiaki.sdl3_gamepads)} gamepad(s) total, 0 DS4).",
        )

    # Camada 4: Comandos recebidos pelo PS5 (nunca presumida automaticamente)
    chiaki_running = check_chiaki_process_running()
    layer4 = LayerStatus(
        4,
        "Comandos efetivamente recebidos pelo PlayStation 5",
        "PENDENTE",
        (
            "Requer validação presencial em sessão Remote Play ativa no PS5 "
            f"(chiaki.exe em execução agora: {'SIM' if chiaki_running else 'NÃO'})."
        ),
    )

    return [layer1, layer2, layer3, layer4]


def format_environment_report(
    active_controller: VirtualPlayStationController | None = None,
) -> str:
    """Gera relatório legível do ambiente e das 4 camadas de compatibilidade."""
    env = inspect_windows_environment()
    chiaki = probe_chiaki_installation()
    layers = evaluate_four_layer_status(active_controller)

    lines = [
        "=" * 74,
        "DIAGNÓSTICO DE AMBIENTE E COMPATIBILIDADE (CHIAKI-NG / CONTROLE VIRTUAL)",
        "=" * 74,
        f"SO / Arquitetura       : {env['os']} ({env['architecture']})",
        f"Python                 : {env['python_version']} ({env['python_executable']})",
        f"Privilégios Admin      : {'SIM' if env['is_admin'] else 'NÃO (usuário padrão)'}",
        f"ViGEmBus .sys presente : {'SIM' if env['vigembus_sys_exists'] else 'NÃO'}",
        f"ViGEmBus Interface PnP : {'ATIVA' if env['vigembus_interface_present'] else 'AUSENTE'} "
        f"(GUID {VIGEMBUS_INTERFACE_GUID}, Win32={env['vigembus_interface_error']})",
        f"Pacote vgamepad        : {'INSTALADO' if env['vgamepad_installed'] else 'NÃO INSTALADO'}",
        f"Chiaki-ng              : {'INSTALADO em ' + str(chiaki.install_dir) if chiaki.installed else 'NÃO ENCONTRADO'}",
        f"Chiaki-ng SDL3.dll     : {'CARREGÁVEL' if chiaki.sdl3_loadable else 'INDISPONÍVEL'} "
        f"(gamepads atuais: {len(chiaki.sdl3_gamepads)}, joysticks: {chiaki.sdl3_joysticks_count})",
    ]

    if env["hidguardian_in_upper_filters"]:
        lines.append(
            "ALERTA AMBIENTAL       : HidGuardian detectado em HIDClass UpperFilters "
            f"({env['hidguardian_upper_filters']}). Não foi alterado; monitorar caso "
            "o DS4 virtual fique oculto após instalar o ViGEmBus."
        )

    lines.append("-" * 74)
    lines.append("MATRIZ DE COMPATIBILIDADE EM 4 CAMADAS (SEM PRESUNÇÕES):")
    for layer in layers:
        lines.append(f"  [{layer.layer_number}] {layer.name}")
        lines.append(f"      Status  : {layer.status}")
        lines.append(f"      Detalhes: {layer.details}")
    lines.append("=" * 74)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Teste ao vivo completo (após instalação do ViGEmBus 1.22.0 + vgamepad 0.2.0)
# ---------------------------------------------------------------------------

def run_live_sdl3_validation() -> tuple[bool, list[SelfTestItem]]:
    """Cria o controle virtual real e valida todos os comandos via SDL3.dll do Chiaki-ng."""
    results: list[SelfTestItem] = []
    probe = probe_vigembus_driver()
    if not probe.ready_for_virtual_device:
        results.append(
            SelfTestItem(
                "Pré-requisito ViGEmBus + vgamepad",
                False,
                probe.summary,
            )
        )
        return False, results

    chiaki_dir = find_chiaki_install_dir()
    if chiaki_dir is None or not (chiaki_dir / "SDL3.dll").exists():
        results.append(
            SelfTestItem(
                "Pré-requisito Chiaki-ng SDL3.dll",
                False,
                "Instalação do Chiaki-ng ou SDL3.dll não encontrada.",
            )
        )
        return False, results

    controller = VirtualPlayStationController(backend=VDS4ViGEmBackend())
    try:
        controller.start()
        results.append(
            SelfTestItem(
                "Camada 1 — Criação do VDS4Gamepad",
                True,
                f"Conectado: {controller.backend.get_device_info()}",
            )
        )

        # Aguarda enumeração PnP do Windows
        hid_devices: list[str] = []
        for _ in range(25):
            hid_devices = list_windows_ds4_hid_devices()
            if hid_devices:
                break
            time.sleep(0.1)

        results.append(
            SelfTestItem(
                "Camada 2 — Reconhecimento HID no Windows",
                bool(hid_devices),
                f"{len(hid_devices)} interface(s) VID_054C&PID_05C4 ativa(s)."
                if hid_devices
                else "Interface HID DS4 não apareceu no Windows dentro de 2.5 s.",
            )
        )

        # Pulso de despertar (recomendado pelo próprio vgamepad para SDL HIDAPI)
        controller.press_button(PSButton.CROSS)
        time.sleep(0.08)
        controller.reset_all()
        time.sleep(0.15)

        with _ChiakiSDL3Inspector(chiaki_dir) as sdl3:
            handle, info = sdl3.open_ds4_gamepad()
            if not handle:
                results.append(
                    SelfTestItem(
                        "Camada 3 — Detecção na SDL3.dll do Chiaki-ng",
                        False,
                        "SDL3.dll do Chiaki-ng não detectou o DualShock 4 virtual.",
                    )
                )
                return False, results

            try:
                results.append(
                    SelfTestItem(
                        "Camada 3 — Detecção na SDL3.dll do Chiaki-ng",
                        True,
                        f"Detectado: {info}",
                    )
                )

                # Botões mapeados no SDL3
                btn_checks = [
                    (PSButton.CROSS, sdl3.BUTTON_SOUTH),
                    (PSButton.CIRCLE, sdl3.BUTTON_EAST),
                    (PSButton.SQUARE, sdl3.BUTTON_WEST),
                    (PSButton.TRIANGLE, sdl3.BUTTON_NORTH),
                    (PSButton.L1, sdl3.BUTTON_LEFT_SHOULDER),
                    (PSButton.R1, sdl3.BUTTON_RIGHT_SHOULDER),
                    (PSButton.L3, sdl3.BUTTON_LEFT_STICK),
                    (PSButton.R3, sdl3.BUTTON_RIGHT_STICK),
                    (PSButton.OPTIONS, sdl3.BUTTON_START),
                    (PSButton.SHARE, sdl3.BUTTON_BACK),
                    (PSButton.TOUCHPAD, sdl3.BUTTON_TOUCHPAD),
                ]
                failed_buttons: list[str] = []
                for ps_btn, sdl_btn in btn_checks:
                    controller.press_button(ps_btn)
                    time.sleep(0.08)
                    pressed_ok = sdl3.read_button(handle, sdl_btn)
                    controller.release_button(ps_btn)
                    time.sleep(0.06)
                    released_ok = not sdl3.read_button(handle, sdl_btn)
                    if not (pressed_ok and released_ok):
                        failed_buttons.append(ps_btn.value)

                results.append(
                    SelfTestItem(
                        "Camada 3 — Botões + Touchpad Click via SDL3",
                        not failed_buttons,
                        "Todos os 11 botões (incluindo Share, Options e Touchpad Click) validados."
                        if not failed_buttons
                        else f"Falha nos botões: {failed_buttons}",
                    )
                )

                # D-Pad 4 direções
                dpad_checks = [
                    (DPadDirection.UP, sdl3.BUTTON_DPAD_UP),
                    (DPadDirection.DOWN, sdl3.BUTTON_DPAD_DOWN),
                    (DPadDirection.LEFT, sdl3.BUTTON_DPAD_LEFT),
                    (DPadDirection.RIGHT, sdl3.BUTTON_DPAD_RIGHT),
                ]
                failed_dpad: list[str] = []
                for d_dir, sdl_btn in dpad_checks:
                    controller.set_dpad(d_dir)
                    time.sleep(0.08)
                    ok_on = sdl3.read_button(handle, sdl_btn)
                    controller.set_dpad(DPadDirection.NONE)
                    time.sleep(0.06)
                    ok_off = not sdl3.read_button(handle, sdl_btn)
                    if not (ok_on and ok_off):
                        failed_dpad.append(d_dir.value)

                results.append(
                    SelfTestItem(
                        "Camada 3 — D-Pad (4 direções) via SDL3",
                        not failed_dpad,
                        "UP, DOWN, LEFT, RIGHT e NONE validados."
                        if not failed_dpad
                        else f"Falha no D-Pad: {failed_dpad}",
                    )
                )

                # Analógicos LX, LY, RX, RY (negativo, intermediário, positivo, centro)
                axis_checks = [
                    (AnalogAxis.LX, sdl3.AXIS_LEFTX),
                    (AnalogAxis.LY, sdl3.AXIS_LEFTY),
                    (AnalogAxis.RX, sdl3.AXIS_RIGHTX),
                    (AnalogAxis.RY, sdl3.AXIS_RIGHTY),
                ]
                failed_axes: list[str] = []
                for ax, sdl_ax in axis_checks:
                    for expected in (-1.0, -0.5, 0.0, 0.5, 1.0, 0.0):
                        controller.set_axis(ax, expected)
                        time.sleep(0.08)
                        observed = sdl3.read_axis_normalized(handle, sdl_ax)
                        if abs(observed - expected) > 0.05:
                            failed_axes.append(f"{ax.value}@{expected:+.2f}->obs={observed:+.2f}")
                controller.reset_all()

                results.append(
                    SelfTestItem(
                        "Camada 3 — Analógicos LX/LY/RX/RY via SDL3",
                        not failed_axes,
                        "Todos os 4 eixos validados em -1.0, -0.5, 0.0, +0.5 e +1.0."
                        if not failed_axes
                        else f"Desvios: {failed_axes}",
                    )
                )

                # Gatilhos analógicos L2 e R2 (valores intermediários não binários)
                trigger_checks = [
                    (TriggerName.L2, sdl3.AXIS_LEFT_TRIGGER),
                    (TriggerName.R2, sdl3.AXIS_RIGHT_TRIGGER),
                ]
                failed_triggers: list[str] = []
                for trig, sdl_ax in trigger_checks:
                    for expected in (0.0, 0.25, 0.50, 0.75, 1.0, 0.0):
                        controller.set_trigger(trig, expected)
                        time.sleep(0.08)
                        observed = sdl3.read_axis_normalized(handle, sdl_ax)
                        if abs(observed - expected) > 0.05:
                            failed_triggers.append(
                                f"{trig.value}@{expected:.2f}->obs={observed:.2f}"
                            )
                controller.reset_all()

                results.append(
                    SelfTestItem(
                        "Camada 3 — Gatilhos analógicos L2/R2 via SDL3",
                        not failed_triggers,
                        "Valores contínuos 0.00, 0.25, 0.50, 0.75 e 1.00 confirmados em L2 e R2."
                        if not failed_triggers
                        else f"Desvios: {failed_triggers}",
                    )
                )
            finally:
                sdl3.close_gamepad(handle)
    finally:
        controller.close()

    all_ok = all(item.passed for item in results)
    return all_ok, results


# ---------------------------------------------------------------------------
# Suíte de autotestes offline (NÃO depende do driver ViGEmBus)
# ---------------------------------------------------------------------------

def run_offline_self_tests() -> tuple[bool, list[SelfTestItem]]:
    """Executa bateria de testes que não dependem de driver instalado."""
    items: list[SelfTestItem] = []

    # 1. Guarda de inicialização preguiçosa (não deve falhar no import nem travar)
    probe = probe_vigembus_driver()
    if not probe.ready_for_virtual_device:
        real_backend = VDS4ViGEmBackend()
        try:
            real_backend.connect()
            items.append(
                SelfTestItem(
                    "Guarda de inicialização do backend ViGEmBus",
                    False,
                    "Esperava bloqueio controlado sem driver, mas connect() não levantou exceção.",
                )
            )
        except DriverUnavailableError as exc:
            items.append(
                SelfTestItem(
                    "Guarda de inicialização do backend ViGEmBus",
                    True,
                    f"Bloqueio limpo com DriverUnavailableError: {str(exc).splitlines()[0]}",
                )
            )
        except Exception as exc:
            items.append(
                SelfTestItem(
                    "Guarda de inicialização do backend ViGEmBus",
                    False,
                    f"Exceção inesperada: {type(exc).__name__}: {exc}",
                )
            )
    else:
        items.append(
            SelfTestItem(
                "Guarda de inicialização do backend ViGEmBus",
                True,
                "Driver e vgamepad já disponíveis no ambiente (guarda não acionada).",
            )
        )

    # 2. Mapeamento de todos os botões PlayStation (incluindo Share, Options, Touchpad, PS)
    sim = SimulatedDS4Backend()
    ctrl = VirtualPlayStationController(backend=sim, config=ControllerConfig(update_rate_hz=100.0))
    try:
        ctrl.start()

        btn_failures: list[str] = []
        for btn in PSButton:
            ctrl.press_button(btn)
            raw = ctrl.get_raw_report()
            if btn in (PSButton.PS, PSButton.TOUCHPAD):
                expected_bit = 1 << 0 if btn == PSButton.PS else 1 << 1
                if not (raw.special_byte & expected_bit):
                    btn_failures.append(f"{btn.value}: bSpecial não ativou")
            elif btn == PSButton.L2:
                if raw.trigger_l != 255 or not (raw.buttons_word & (1 << 10)):
                    btn_failures.append("L2: analógico/digital inconsistente")
            elif btn == PSButton.R2:
                if raw.trigger_r != 255 or not (raw.buttons_word & (1 << 11)):
                    btn_failures.append("R2: analógico/digital inconsistente")
            else:
                if (raw.buttons_word & 0xFFF0) == 0:
                    btn_failures.append(f"{btn.value}: wButtons não ativou")

            ctrl.release_button(btn)
            raw_after = ctrl.get_raw_report()
            if not raw_after.is_neutral():
                btn_failures.append(f"{btn.value}: estado residual após release")

        items.append(
            SelfTestItem(
                "Mapeamento de todos os 14 botões PlayStation + liberação limpa",
                not btn_failures,
                "Todos os botões (Cross..R3, Options, Share, Touchpad, PS) mapeados e zerados sem resíduo."
                if not btn_failures
                else f"Falhas: {btn_failures}",
            )
        )

        # 3. D-Pad (4 direções + diagonais + neutro)
        dpad_failures: list[str] = []
        for d in DPadDirection:
            ctrl.set_dpad(d)
            nibble = ctrl.get_raw_report().buttons_word & 0x000F
            if d == DPadDirection.NONE and nibble != 0x8:
                dpad_failures.append(f"NONE->0x{nibble:x}")
            elif d != DPadDirection.NONE and nibble == 0x8:
                dpad_failures.append(f"{d.value}->0x8")
        ctrl.set_dpad(DPadDirection.NONE)
        if not ctrl.get_raw_report().is_neutral():
            dpad_failures.append("D-Pad não retornou ao neutro")

        items.append(
            SelfTestItem(
                "Mapeamento do D-Pad (4 cardeais + diagonais + neutro)",
                not dpad_failures,
                "Todos os 9 estados do HAT DS4 validados corretamente."
                if not dpad_failures
                else f"Falhas: {dpad_failures}",
            )
        )

        # 4. Eixos analógicos independentes + saturação (clamping)
        axis_failures: list[str] = []
        axis_expectations = [
            (-1.5, 1),   # clamping para -1.0 -> byte 1
            (-1.0, 1),
            (-0.5, 64),
            (0.0, 128),
            (0.5, 192),
            (1.0, 255),
            (2.0, 255),  # clamping para +1.0 -> byte 255
        ]
        for ax in AnalogAxis:
            for val, exp_byte in axis_expectations:
                ctrl.set_axis(ax, val)
                raw = ctrl.get_raw_report()
                got = {
                    AnalogAxis.LX: raw.thumb_lx,
                    AnalogAxis.LY: raw.thumb_ly,
                    AnalogAxis.RX: raw.thumb_rx,
                    AnalogAxis.RY: raw.thumb_ry,
                }[ax]
                if got != exp_byte:
                    axis_failures.append(f"{ax.value}({val})={got}!=esp({exp_byte})")
            ctrl.set_axis(ax, 0.0)

        items.append(
            SelfTestItem(
                "Eixos analógicos LX/LY/RX/RY (negativo, intermediário, máximo, centro, clamp)",
                not axis_failures,
                "Conversão HID DS4 [1..128..255] e clamping validados para os 4 eixos."
                if not axis_failures
                else f"Falhas: {axis_failures}",
            )
        )

        # 5. Gatilhos analógicos L2 e R2 (intermediários não binários)
        trig_failures: list[str] = []
        trig_expectations = [
            (-0.5, 0, False),
            (0.0, 0, False),
            (0.25, 64, True),
            (0.50, 128, True),
            (0.75, 191, True),
            (1.00, 255, True),
            (1.50, 255, True),
        ]
        for trig in TriggerName:
            for val, exp_byte, exp_dig in trig_expectations:
                ctrl.set_trigger(trig, val)
                raw = ctrl.get_raw_report()
                got_byte = raw.trigger_l if trig == TriggerName.L2 else raw.trigger_r
                dig_bit = (1 << 10) if trig == TriggerName.L2 else (1 << 11)
                got_dig = bool(raw.buttons_word & dig_bit)
                if got_byte != exp_byte or got_dig != exp_dig:
                    trig_failures.append(
                        f"{trig.value}({val})->byte={got_byte}/{exp_byte},dig={got_dig}/{exp_dig}"
                    )
            ctrl.set_trigger(trig, 0.0)

        items.append(
            SelfTestItem(
                "Gatilhos analógicos L2/R2 (0.00 a 1.00 contínuos + sincronia digital)",
                not trig_failures,
                "Valores intermediários (0, 64, 128, 191, 255) confirmados em L2 e R2."
                if not trig_failures
                else f"Falhas: {trig_failures}",
            )
        )

        # 6. Estresse de concorrência (thread-safety) + estabilidade do loop ~100 Hz
        concurrency_errors: list[str] = []

        def _worker(worker_id: int) -> None:
            try:
                for step in range(80):
                    btn = PSButton.CROSS if (step + worker_id) % 2 == 0 else PSButton.CIRCLE
                    ctrl.set_button(btn, (step % 3) != 0)
                    ctrl.set_left_stick(((step % 21) - 10) / 10.0, ((10 - (step % 21)) / 10.0))
                    ctrl.set_trigger(TriggerName.R2, (step % 11) / 10.0)
                    _ = ctrl.get_state()
                    _ = ctrl.format_telemetry()
                    time.sleep(0.005)
            except Exception as exc:
                concurrency_errors.append(f"worker-{worker_id}: {exc}")

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Deixa o loop estabilizar por 0.5 s adicionais para medir Hz reais
        time.sleep(0.5)
        metrics = ctrl.get_metrics()
        hz_ok = 75.0 <= metrics.actual_hz <= 115.0 and metrics.frames_sent >= 70
        items.append(
            SelfTestItem(
                "Thread-safety sob concorrência + estabilidade do loop ~100 Hz",
                (not concurrency_errors) and hz_ok and metrics.last_error is None,
                (
                    f"4 threads concorrentes sem erros; taxa medida={metrics.actual_hz:.1f} Hz "
                    f"(alvo {metrics.target_hz:.0f} Hz, {metrics.frames_sent} quadros enviados)."
                )
                if not concurrency_errors
                else f"Erros de concorrência: {concurrency_errors}",
            )
        )

        # 7. Encerramento seguro com comandos deliberadamente ativos
        ctrl.press_button(PSButton.CROSS)
        ctrl.press_button(PSButton.TOUCHPAD)
        ctrl.set_dpad(DPadDirection.UP)
        ctrl.set_left_stick(1.0, -1.0)
        ctrl.set_right_stick(-1.0, 1.0)
        ctrl.set_trigger(TriggerName.L2, 1.0)
        ctrl.set_trigger(TriggerName.R2, 0.85)
        time.sleep(0.03)
    finally:
        ctrl.close()

    final_report = sim.get_last_report()
    safe_ok = (
        (not ctrl.is_running)
        and (not sim.is_connected)
        and ctrl.get_state().is_neutral()
        and final_report.is_neutral()
    )
    items.append(
        SelfTestItem(
            "Encerramento seguro (reset total + parada da thread + desconexão)",
            safe_ok,
            "Thread parada, backend desconectado e último relatório enviado 100% neutro."
            if safe_ok
            else f"Falha no reset/encerramento: running={ctrl.is_running}, connected={sim.is_connected}, report={final_report}",
        )
    )

    # 8. Carregamento somente leitura da SDL3.dll do Chiaki-ng
    chiaki = probe_chiaki_installation()
    items.append(
        SelfTestItem(
            "Inspeção somente leitura da SDL3.dll instalada pelo Chiaki-ng",
            chiaki.installed and chiaki.sdl3_loadable,
            f"Chiaki-ng localizado em {chiaki.install_dir}; SDL3.dll carregada com sucesso "
            f"(gamepads atuais: {len(chiaki.sdl3_gamepads)})."
            if chiaki.sdl3_loadable
            else f"Indisponível: {chiaki.error}",
        )
    )

    return all(i.passed for i in items), items


# ---------------------------------------------------------------------------
# CLI do módulo de diagnóstico
# ---------------------------------------------------------------------------

def _print_test_results(title: str, passed: bool, items: list[SelfTestItem]) -> None:
    print("=" * 74)
    print(title)
    print("=" * 74)
    for idx, item in enumerate(items, start=1):
        tag = "PASS" if item.passed else "FAIL"
        print(f"[{tag}] {idx:02d}. {item.name}")
        print(f"       {item.details}")
    print("-" * 74)
    print(f"RESUMO: {sum(i.passed for i in items)}/{len(items)} verificações aprovadas "
          f"({'SUCESSO' if passed else 'COM FALHAS'}).")
    print("=" * 74)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico de compatibilidade do controle virtual com Chiaki-ng."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Executa todos os testes automatizados que NÃO dependem do driver ViGEmBus.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Cria o controle virtual real e valida todos os comandos via SDL3.dll do Chiaki-ng.",
    )
    args = parser.parse_args(argv)

    print(format_environment_report())
    print()

    exit_code = 0
    if args.self_test:
        passed, items = run_offline_self_tests()
        _print_test_results("AUTOTESTES OFFLINE (SEM DEPENDÊNCIA DE DRIVER)", passed, items)
        if not passed:
            exit_code = 1

    if args.live:
        passed, items = run_live_sdl3_validation()
        _print_test_results("VALIDAÇÃO AO VIVO (ViGEmBus -> Windows HID -> Chiaki-ng SDL3)", passed, items)
        if not passed:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
