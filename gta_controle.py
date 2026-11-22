"""Conversor Teclado + Mouse/Trackpad -> Controle Virtual PlayStation (GTA no PS5).

Características principais:
    - Carrega todo o mapeamento de teclas e sensibilidades do arquivo editável
      `mapeamento.json` (você pode remapear qualquer tecla no Bloco de Notas).
    - Suporta tanto **Mouse USB/Sem Fio** (via Win32 Raw Input `WM_INPUT`) quanto
      **Trackpad de Notebook** (via captura híbrida `GetCursorPos` + recentralização
      suave `SetCursorPos`, sem travar nas bordas da tela).
    - Conversor radial 2D de câmera (`RX/RY`) com compensação de deadzone do GTA,
      multiplicador de sensibilidade ao mirar (ADS) e retorno exato ao centro
      (`0.00 / HID 128`) quando não há movimento.
    - Máquina de estados exclusiva (`modo_veiculo` iniciando em `False`) sem
      sobreposição de teclas duplas, com rampa suave de ~100 ms no `R2`,
      `F` (Triângulo + troca modo), `F1` (troca manual silenciosa) e `F12` (pausa).

Uso rápido:
    Duplo clique em `iniciar_gta.bat` ou:
        python gta_controle.py              # Modo real (foco automático no Chiaki-ng)
        python gta_controle.py --global     # Atua em qualquer janela (ex.: joy.cpl)
        python gta_controle.py --self-test  # Autotestes automatizados
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


def get_app_dir() -> Path:
    """Retorna a pasta do executável (quando compilado em .exe) ou do script .py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _ensure_project_venv() -> None:
    """Se executado fora do `.venv` (ex.: duplo clique no .py), relança no `.venv`."""
    if getattr(sys, "frozen", False):
        return
    if "--simulate" in sys.argv:
        return
    if importlib.util.find_spec("vgamepad") is not None:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return
    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return
    except OSError:
        return
    raise SystemExit(
        subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    )


if __name__ == "__main__":
    _ensure_project_venv()

import argparse
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import json
import math
import platform
import signal
import threading
import time
from typing import Any, Callable

from config import (
    ControllerConfig,
    DPadDirection,
    PSButton,
    VIGEMBUS_RECOMMENDED_VERSION,
    VIGEMBUS_RELEASE_URL,
)
from gamepad import (
    BackendDependencyError,
    ControllerState,
    DriverUnavailableError,
    SimulatedDS4Backend,
    VDS4ViGEmBackend,
    VirtualPlayStationController,
    _WindowsHighResTimer,
    clamp_float,
    probe_vigembus_driver,
)


# ===========================================================================
# Arquivo padrão de remapeamento editável pelo usuário
# ===========================================================================

DEFAULT_MAPPING_FILE = get_app_dir() / "mapeamento.json"
PROCESSOS_ALVO_FOCO = (
    "chiaki.exe",
    "chiaki-ng.exe",
    "remoteplay.exe",
    "psremoteplay.exe",
    "pxplay.exe",
    "psplay.exe",
)
EXACT_FALLBACK_TITLES = (
    "chiaki",
    "chiaki-ng",
    "ps remote play",
    "pxplay",
    "psplay",
)


def is_remote_play_window(proc_name: str, title: str) -> bool:
    """Retorna True SOMENTE se a janela em foco for de um cliente Remote Play real.

    Quando `proc_name` está disponível (quase sempre no Windows), valida
    EXCLUSIVAMENTE pelo nome exato do executável (`chiaki.exe`, `remoteplay.exe`,
    `pxplay.exe` etc.) para impedir falsos positivos em pastas, editores ou abas
    de navegador que contenham 'remote play' ou 'chiaki' no título!
    """
    pname = (proc_name or "").strip().lower()
    if pname:
        return pname in PROCESSOS_ALVO_FOCO
    # Fallback estrito apenas se o Windows não retornou o nome do processo
    tlow = (title or "").strip().lower()
    return (tlow in EXACT_FALLBACK_TITLES) or tlow.startswith(
        ("chiaki |", "chiaki-ng", "ps remote play", "pxplay", "psplay")
    )


# ===========================================================================
# Tabela de nomes amigáveis de teclas -> códigos Virtual-Key (Win32)
# ===========================================================================

MOUSE_LEFT = -1
MOUSE_RIGHT = -2
MOUSE_MIDDLE = -3
MOUSE_X1 = -4
MOUSE_X2 = -5
SCROLL_UP = -6
SCROLL_DOWN = -7

KEY_NAME_TO_CODE: dict[str, int] = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "CAPSLOCK": 0x14,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "ESPACO": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "LSHIFT": 0xA0,
    "SHIFT": 0xA0,
    "RSHIFT": 0xA1,
    "LCTRL": 0xA2,
    "CTRL": 0xA2,
    "RCTRL": 0xA3,
    "LALT": 0xA4,
    "CEDILHA": 0xBA,
    "Ç": 0xBA,
    "COMMA": 0xBC,
    "MINUS": 0xBD,
    "PERIOD": 0xBE,
    "SLASH": 0xBF,
    "ABNT_SLASH": 0xC1,
    "OEM_102": 0xE2,
    "NUM_ADD": 0x6B,
    "NUM_SUB": 0x6D,
    "MOUSE_LEFT": MOUSE_LEFT,
    "MOUSE_ESQ": MOUSE_LEFT,
    "LMB": MOUSE_LEFT,
    "MOUSE_RIGHT": MOUSE_RIGHT,
    "MOUSE_DIR": MOUSE_RIGHT,
    "RMB": MOUSE_RIGHT,
    "MOUSE_MIDDLE": MOUSE_MIDDLE,
    "MOUSE_MEIO": MOUSE_MIDDLE,
    "MMB": MOUSE_MIDDLE,
    "MOUSE_X1": MOUSE_X1,
    "MOUSE_4": MOUSE_X1,
    "MOUSE_X2": MOUSE_X2,
    "MOUSE_5": MOUSE_X2,
    "SCROLL_UP": SCROLL_UP,
    "SCROLL_DOWN": SCROLL_DOWN,
}

# Letras A-Z e dígitos 0-9
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    KEY_NAME_TO_CODE[_ch] = ord(_ch)

# Numpad 0-9
for _n in range(10):
    KEY_NAME_TO_CODE[f"NUM{_n}"] = 0x60 + _n

# Teclas de função F1-F12
for _fn in range(1, 13):
    KEY_NAME_TO_CODE[f"F{_fn}"] = 0x6F + _fn

CODE_TO_KEY_NAME: dict[int, str] = {}
for _name, _code in KEY_NAME_TO_CODE.items():
    CODE_TO_KEY_NAME.setdefault(_code, _name)

VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "CROSS",
        "CROSS_TAP",
        "CIRCLE",
        "SQUARE",
        "TRIANGLE",
        "L1",
        "R1",
        "L2",
        "L2_RAMPA",
        "L2_TOGGLE",
        "R2",
        "R2_RAMPA",
        "L3",
        "R3",
        "L3+R3",
        "OPTIONS",
        "SHARE",
        "PS",
        "TOUCHPAD_TAP",
        "TOUCHPAD_HOLD_500",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_LEFT",
        "DPAD_RIGHT",
        "LX-",
        "LX+",
        "LY+",
        "LY-",
        "MOD_LENTO",
    }
)


def parse_key_name(name: str) -> int:
    key = name.strip().upper()
    if key not in KEY_NAME_TO_CODE:
        raise ValueError(
            f"Tecla desconhecida em mapeamento.json: '{name}'. "
            "Exemplos válidos: W, A, S, D, SPACE, LSHIFT, LCTRL, MOUSE_LEFT, MOUSE_RIGHT, F1, F12."
        )
    return KEY_NAME_TO_CODE[key]


def parse_action_name(action: str) -> str:
    act = action.strip().upper()
    if act not in VALID_ACTIONS:
        raise ValueError(
            f"Ação desconhecida em mapeamento.json: '{action}'. "
            f"Ações válidas: {', '.join(sorted(VALID_ACTIONS))}"
        )
    return act


# ===========================================================================
# Configuração consolidada (carregada de mapeamento.json)
# ===========================================================================

@dataclass
class GtaConfig:
    """Parâmetros ajustáveis e tabelas de mapeamento carregados do JSON."""

    mouse_sens_x: float = 0.220
    mouse_sens_y: float = 0.180
    mouse_mult_ads: float = 1.25
    mouse_deadzone_entrada: float = 0.0
    mouse_deadzone_compensacao: float = 0.18
    mouse_curva_expo: float = 0.65
    mouse_suavizacao: float = 0.0
    mouse_decaimento_parada: float = 0.88
    mouse_timeout_centro_ms: float = 45.0
    inverter_y_mouse: bool = False
    usar_captura_cursor_trackpad: bool = True

    tempo_rampa_r2_ms: float = 100.0
    tempo_rampa_l2_ms: float = 100.0
    tempo_rampa_direcao_lx_ms: float = 90.0
    fator_caminhada_lenta: float = 0.45
    scroll_pulso_ms: float = 75.0
    sprint_tap_ms: float = 65.0
    touchpad_hold_m_ms: float = 500.0
    touchpad_tap_v_ms: float = 80.0
    triangle_tap_f_ms: float = 90.0

    taxa_atualizacao_hz: float = 200.0
    inverter_y_esquerdo: bool = False
    iniciar_ativo: bool = True
    apenas_chiaki_em_foco: bool = True
    bloquear_entradas_windows: bool = True
    exigir_soltar_ao_trocar_modo: bool = True
    feedback_sonoro: bool = True
    mostrar_mini_overlay: bool = True
    idioma: str = "pt"

    # Controles de sistema
    vk_toggle_mode_triangle: int = KEY_NAME_TO_CODE["F"]
    vk_toggle_mode_silent: int = KEY_NAME_TO_CODE["F1"]
    vk_toggle_flight: int = KEY_NAME_TO_CODE["F2"]
    vk_sens_down: int = KEY_NAME_TO_CODE["F7"]
    vk_sens_up: int = KEY_NAME_TO_CODE["F8"]
    vk_toggle_pause: int = KEY_NAME_TO_CODE["F12"]

    # Mapas key_code -> action_name
    globais: dict[int, str] = field(default_factory=dict)
    modo_a_pe: dict[int, str] = field(default_factory=dict)
    modo_veiculo: dict[int, str] = field(default_factory=dict)
    modo_voo: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.globais and not self.modo_a_pe and not self.modo_veiculo and not self.modo_voo:
            self._fill_default_maps()

    def _fill_default_maps(self) -> None:
        self.globais = {
            KEY_NAME_TO_CODE["A"]: "LX-",
            KEY_NAME_TO_CODE["D"]: "LX+",
            KEY_NAME_TO_CODE["TAB"]: "L1",
            KEY_NAME_TO_CODE["V"]: "TOUCHPAD_TAP",
            KEY_NAME_TO_CODE["M"]: "TOUCHPAD_HOLD_500",
            KEY_NAME_TO_CODE["C"]: "R3",
            KEY_NAME_TO_CODE["ESC"]: "OPTIONS",
            KEY_NAME_TO_CODE["P"]: "OPTIONS",
            KEY_NAME_TO_CODE["H"]: "DPAD_RIGHT",
            KEY_NAME_TO_CODE["ENTER"]: "CROSS",
            KEY_NAME_TO_CODE["BACKSPACE"]: "CIRCLE",
            KEY_NAME_TO_CODE["CAPSLOCK"]: "L3+R3",
            KEY_NAME_TO_CODE["UP"]: "DPAD_UP",
            KEY_NAME_TO_CODE["DOWN"]: "DPAD_DOWN",
            KEY_NAME_TO_CODE["LEFT"]: "DPAD_LEFT",
            KEY_NAME_TO_CODE["RIGHT"]: "DPAD_RIGHT",
        }
        self.modo_a_pe = {
            KEY_NAME_TO_CODE["W"]: "LY+",
            KEY_NAME_TO_CODE["S"]: "LY-",
            KEY_NAME_TO_CODE["LSHIFT"]: "CROSS",
            KEY_NAME_TO_CODE["SPACE"]: "SQUARE",
            KEY_NAME_TO_CODE["LCTRL"]: "L3",
            KEY_NAME_TO_CODE["Q"]: "R1",
            KEY_NAME_TO_CODE["R"]: "CIRCLE",
            KEY_NAME_TO_CODE["E"]: "DPAD_RIGHT",
            MOUSE_RIGHT: "L2",
            MOUSE_LEFT: "R2",
        }
        self.modo_veiculo = {
            KEY_NAME_TO_CODE["W"]: "R2_RAMPA",
            KEY_NAME_TO_CODE["S"]: "L2",
            KEY_NAME_TO_CODE["SPACE"]: "R1",
            KEY_NAME_TO_CODE["LSHIFT"]: "LY+",
            KEY_NAME_TO_CODE["LCTRL"]: "LY-",
            MOUSE_LEFT: "L1",
            MOUSE_RIGHT: "R1",
            KEY_NAME_TO_CODE["Q"]: "CIRCLE",
            KEY_NAME_TO_CODE["E"]: "L3",
        }
        self.modo_voo = self._default_flight_map()

    @staticmethod
    def _default_flight_map() -> dict[int, str]:
        return {
            KEY_NAME_TO_CODE["W"]: "R2_RAMPA",
            KEY_NAME_TO_CODE["S"]: "L2_RAMPA",
            KEY_NAME_TO_CODE["A"]: "L1",
            KEY_NAME_TO_CODE["D"]: "R1",
            KEY_NAME_TO_CODE["UP"]: "LY+",
            KEY_NAME_TO_CODE["DOWN"]: "LY-",
            KEY_NAME_TO_CODE["LEFT"]: "LX-",
            KEY_NAME_TO_CODE["RIGHT"]: "LX+",
            KEY_NAME_TO_CODE["NUM8"]: "LY+",
            KEY_NAME_TO_CODE["NUM5"]: "LY-",
            KEY_NAME_TO_CODE["NUM4"]: "LX-",
            KEY_NAME_TO_CODE["NUM6"]: "LX+",
            KEY_NAME_TO_CODE["SPACE"]: "CROSS",
            MOUSE_LEFT: "CROSS",
            MOUSE_RIGHT: "CIRCLE",
            KEY_NAME_TO_CODE["G"]: "L3",
            KEY_NAME_TO_CODE["TAB"]: "SQUARE",
        }

    def all_managed_keyboard_vks(self) -> frozenset[int]:
        codes: set[int] = {
            self.vk_toggle_mode_triangle,
            self.vk_toggle_mode_silent,
            self.vk_toggle_flight,
            self.vk_sens_down,
            self.vk_sens_up,
        }
        for table in (self.globais, self.modo_a_pe, self.modo_veiculo, self.modo_voo):
            for k in table:
                if k > 0:
                    codes.add(k)
        codes.discard(self.vk_toggle_pause)
        return frozenset(codes)

    def all_managed_mouse_codes(self) -> frozenset[int]:
        codes: set[int] = set()
        for table in (self.globais, self.modo_a_pe, self.modo_veiculo, self.modo_voo):
            for k in table:
                if k < 0:
                    codes.add(k)
        return frozenset(codes)

    def dual_context_keys(self) -> frozenset[int]:
        return frozenset(
            set(self.modo_a_pe.keys())
            | set(self.modo_veiculo.keys())
            | set(self.modo_voo.keys())
        )


def load_config_from_json(path: Path = DEFAULT_MAPPING_FILE) -> GtaConfig:
    """Lê e valida `mapeamento.json`; usa padrões seguros se o arquivo não existir."""
    if not path.exists():
        return GtaConfig()

    raw = json.loads(path.read_text(encoding="utf-8"))
    mouse = raw.get("mouse_e_trackpad", {})
    aj = raw.get("ajustes", {})
    sys_keys = raw.get("controles_sistema", {})

    def _parse_map(section_name: str) -> dict[int, str]:
        sec = raw.get(section_name, {})
        result: dict[int, str] = {}
        for k_name, act_name in sec.items():
            if str(k_name).startswith("_"):
                continue
            result[parse_key_name(str(k_name))] = parse_action_name(str(act_name))
        return result

    cfg = GtaConfig(
        mouse_sens_x=float(mouse.get("sensibilidade_x", 0.220)),
        mouse_sens_y=float(mouse.get("sensibilidade_y", 0.180)),
        mouse_mult_ads=float(mouse.get("mult_mira_ads", 1.25)),
        mouse_deadzone_entrada=float(mouse.get("deadzone_entrada", 0.0)),
        mouse_deadzone_compensacao=float(mouse.get("deadzone_compensacao", 0.18)),
        mouse_curva_expo=float(mouse.get("curva_expo", 0.65)),
        mouse_suavizacao=float(mouse.get("suavizacao", 0.0)),
        mouse_decaimento_parada=float(mouse.get("decaimento_parada", 0.88)),
        mouse_timeout_centro_ms=float(mouse.get("timeout_centro_ms", 45.0)),
        inverter_y_mouse=bool(mouse.get("inverter_y", False)),
        usar_captura_cursor_trackpad=bool(mouse.get("usar_captura_cursor_trackpad", True)),
        tempo_rampa_r2_ms=float(aj.get("tempo_rampa_r2_ms", 100.0)),
        tempo_rampa_l2_ms=float(aj.get("tempo_rampa_l2_ms", 100.0)),
        tempo_rampa_direcao_lx_ms=float(aj.get("tempo_rampa_direcao_lx_ms", 90.0)),
        fator_caminhada_lenta=float(aj.get("fator_caminhada_lenta", 0.45)),
        scroll_pulso_ms=float(aj.get("scroll_pulso_ms", 75.0)),
        sprint_tap_ms=float(aj.get("sprint_tap_ms", 65.0)),
        touchpad_hold_m_ms=float(aj.get("touchpad_segurar_m_ms", 500.0)),
        touchpad_tap_v_ms=float(aj.get("touchpad_toque_v_ms", 80.0)),
        triangle_tap_f_ms=float(aj.get("triangulo_pulso_f_ms", 90.0)),
        taxa_atualizacao_hz=float(aj.get("taxa_atualizacao_hz", 200.0)),
        inverter_y_esquerdo=bool(aj.get("inverter_y_esquerdo", False)),
        iniciar_ativo=bool(aj.get("iniciar_ativo", True)),
        apenas_chiaki_em_foco=bool(aj.get("apenas_quando_chiaki_em_foco", True)),
        bloquear_entradas_windows=bool(aj.get("bloquear_entradas_no_windows", True)),
        exigir_soltar_ao_trocar_modo=bool(aj.get("exigir_soltar_tecla_ao_trocar_modo", True)),
        feedback_sonoro=bool(aj.get("feedback_sonoro", True)),
        mostrar_mini_overlay=bool(aj.get("mostrar_mini_overlay", True)),
        idioma=str(aj.get("idioma", "pt")).strip().lower() if str(aj.get("idioma", "pt")).strip().lower() in ("pt", "en") else "pt",
        vk_toggle_mode_triangle=parse_key_name(sys_keys.get("alternar_modo_e_triangulo", "F")),
        vk_toggle_mode_silent=parse_key_name(sys_keys.get("alternar_modo_silencioso", "F1")),
        vk_toggle_flight=parse_key_name(sys_keys.get("alternar_modo_voo", "F2")),
        vk_sens_down=parse_key_name(sys_keys.get("sensibilidade_menos", "F7")),
        vk_sens_up=parse_key_name(sys_keys.get("sensibilidade_mais", "F8")),
        vk_toggle_pause=parse_key_name(sys_keys.get("pausar_ativar", "F12")),
        globais=_parse_map("globais"),
        modo_a_pe=_parse_map("modo_a_pe"),
        modo_veiculo=_parse_map("modo_veiculo"),
        modo_voo=_parse_map("modo_voo") if isinstance(raw.get("modo_voo"), dict) else GtaConfig._default_flight_map(),
    )
    return cfg


def _beep_async(notes: tuple[tuple[int, int], ...], enabled: bool) -> None:
    if not enabled or platform.system() != "Windows":
        return

    def _run() -> None:
        try:
            import winsound  # noqa: PLC0415

            for freq, dur_ms in notes:
                winsound.Beep(freq, dur_ms)
        except Exception:
            pass

    threading.Thread(target=_run, name="AudioFeedback", daemon=True).start()


def check_chiaki_uipi_mismatch() -> str | None:
    """Detecta (somente leitura) se chiaki.exe tem RUNASADMIN enquanto este script não é Admin."""
    if platform.system() != "Windows":
        return None
    import winreg  # noqa: PLC0415

    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    if is_admin:
        return None

    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(
                root, r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
            ) as k:
                idx = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, idx)
                        idx += 1
                        if "chiaki.exe" in str(name).lower() and "RUNASADMIN" in str(val).upper():
                            return (
                                "BLOQUEIO UIPI: chiaki.exe está configurado como RUNASADMIN, "
                                "mas este script não está como Administrador! "
                                "Desmarque 'Executar como administrador' em chiaki.exe OU "
                                "clique com botão direito em iniciar_gta.bat -> Executar como administrador."
                            )
                    except OSError:
                        break
        except OSError:
            continue
    return None


def check_chiaki_conflicting_keymap() -> list[str]:
    """Verifica (somente leitura) se o Chiaki-ng tem atalhos de teclado conflitantes."""
    if platform.system() != "Windows":
        return []
    import winreg  # noqa: PLC0415

    conflicts: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Chiaki\Chiaki\keymap") as k:
            idx = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, idx)
                    idx += 1
                    if str(val).strip():
                        conflicts.append(f"{name}={val}")
                except OSError:
                    break
    except OSError:
        return []
    return conflicts


def read_chiaki_settings_readonly() -> dict[str, Any]:
    """Lê (somente leitura) parâmetros de vídeo/input do Chiaki-ng no Registro do Windows."""
    if platform.system() != "Windows":
        return {}
    import winreg  # noqa: PLC0415

    wanted = (
        "fps_local_ps5",
        "fps_remote_ps5",
        "vsync",
        "mouse_touch_enabled",
        "keyboard_enabled",
        "hw_decoder",
        "codec_local_ps5",
        "resolution_local_ps5",
        "bitrate_local_ps5",
    )
    out: dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Chiaki\Chiaki\settings") as k:
            for name in wanted:
                try:
                    val, _ = winreg.QueryValueEx(k, name)
                    out[name] = val
                except OSError:
                    continue
    except OSError:
        return {}
    return out


def _reg_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _reg_int(val: Any, default: int = 0) -> int:
    try:
        return int(str(val).strip())
    except Exception:
        return default


def run_latency_health_check() -> list[dict[str, str]]:
    """Semáforo de Latência (somente leitura): avalia driver, UIPI e ajustes do Chiaki-ng."""
    items: list[dict[str, str]] = []

    # 1. ViGEmBus Driver
    probe = probe_vigembus_driver()
    items.append(
        {
            "id": "vigembus",
            "status": "green" if probe.interface_present else "red",
            "title_pt": "Driver Virtual ViGEmBus 1.22.0",
            "title_en": "ViGEmBus 1.22.0 Virtual Driver",
            "detail_pt": "Interface PnP ativa e pronta." if probe.interface_present else "Não detectado no Windows.",
            "detail_en": "PnP interface active and ready." if probe.interface_present else "Not detected on Windows.",
            "tip_pt": "OK — nenhuma ação necessária." if probe.interface_present else "Instale o ViGEmBus 1.22.0 WHQL oficial.",
            "tip_en": "OK — no action needed." if probe.interface_present else "Install official ViGEmBus 1.22.0 WHQL.",
        }
    )

    # 2. UIPI / Admin mismatch
    uipi = check_chiaki_uipi_mismatch()
    items.append(
        {
            "id": "uipi",
            "status": "red" if uipi else "green",
            "title_pt": "Permissão UIPI (Chiaki-ng vs Controle)",
            "title_en": "UIPI Privilege Match (Chiaki-ng vs App)",
            "detail_pt": "chiaki.exe configurado como Administrador (bloqueia captura!)." if uipi else "Sem bloqueio UIPI detectado.",
            "detail_en": "chiaki.exe set as Administrator (blocks hooks!)." if uipi else "No UIPI privilege mismatch.",
            "tip_pt": "Desmarque 'Executar como administrador' em chiaki.exe ou execute este app como Admin." if uipi else "OK — captura de teclado e mouse livre.",
            "tip_en": "Uncheck 'Run as administrator' on chiaki.exe or run this app as Admin." if uipi else "OK — keyboard & mouse hooks unobstructed.",
        }
    )

    # 3. Keymap interno do Chiaki-ng
    conflicts = check_chiaki_conflicting_keymap()
    items.append(
        {
            "id": "keymap",
            "status": "yellow" if conflicts else "green",
            "title_pt": "Keymap Interno do Chiaki-ng",
            "title_en": "Chiaki-ng Internal Keymap",
            "detail_pt": f"{len(conflicts)} tecla(s) configurada(s) no Chiaki-ng." if conflicts else "Limpo (0 conflitos).",
            "detail_en": f"{len(conflicts)} key(s) bound inside Chiaki-ng." if conflicts else "Clean (0 conflicts).",
            "tip_pt": "Limpe as teclas em Chiaki-ng -> Settings -> Keymap para não duplicar comandos." if conflicts else "OK — sem duplicação de teclas.",
            "tip_en": "Clear keys in Chiaki-ng -> Settings -> Keymap to avoid double input." if conflicts else "OK — no duplicate inputs.",
        }
    )

    # 4..7 Configurações de Vídeo/Input do Chiaki-ng (se presentes no Registro)
    ch = read_chiaki_settings_readonly()
    if ch:
        fps_loc = _reg_int(ch.get("fps_local_ps5"), 0)
        fps_rem = _reg_int(ch.get("fps_remote_ps5"), 0)
        fps_ok = (fps_loc >= 60) and (fps_rem == 0 or fps_rem >= 60)
        items.append(
            {
                "id": "fps",
                "status": "green" if fps_ok else "yellow",
                "title_pt": "Taxa de Quadros do Chiaki-ng (FPS)",
                "title_en": "Chiaki-ng Stream Frame Rate (FPS)",
                "detail_pt": f"Local={fps_loc or '?'} FPS | Remoto={fps_rem or '?'} FPS",
                "detail_en": f"Local={fps_loc or '?'} FPS | Remote={fps_rem or '?'} FPS",
                "tip_pt": "OK — 60 FPS reduz drasticamente o atraso da mira." if fps_ok else "Suba para 60 FPS em Chiaki-ng -> Settings -> Video para reduzir ~16 ms de lag!",
                "tip_en": "OK — 60 FPS cuts aim latency significantly." if fps_ok else "Raise to 60 FPS in Chiaki-ng -> Settings -> Video to cut ~16 ms latency!",
            }
        )

        vsync_on = _reg_bool(ch.get("vsync"), False)
        items.append(
            {
                "id": "vsync",
                "status": "yellow" if vsync_on else "green",
                "title_pt": "VSync do Chiaki-ng",
                "title_en": "Chiaki-ng VSync",
                "detail_pt": "Ativado (adiciona 1–2 quadros de atraso)." if vsync_on else "Desativado (menor latência).",
                "detail_en": "Enabled (adds 1–2 frames of latency)." if vsync_on else "Disabled (lowest latency).",
                "tip_pt": "Desative VSync em Chiaki-ng -> Settings -> Video." if vsync_on else "OK — sem atraso extra de VSync.",
                "tip_en": "Disable VSync in Chiaki-ng -> Settings -> Video." if vsync_on else "OK — no VSync buffering lag.",
            }
        )

        mtouch = _reg_bool(ch.get("mouse_touch_enabled"), False)
        kb_en = _reg_bool(ch.get("keyboard_enabled"), False)
        inp_bad = mtouch or kb_en
        items.append(
            {
                "id": "chiaki_input",
                "status": "yellow" if inp_bad else "green",
                "title_pt": "Mouse Touch / Teclado Nativo do Chiaki-ng",
                "title_en": "Chiaki-ng Native Mouse Touch / Keyboard",
                "detail_pt": f"mouse_touch={mtouch}, keyboard={kb_en}",
                "detail_en": f"mouse_touch={mtouch}, keyboard={kb_en}",
                "tip_pt": "Desative 'Mouse Touch' e 'Keyboard' nas opções do Chiaki-ng." if inp_bad else "OK — sem interferência no mouse/trackpad.",
                "tip_en": "Disable 'Mouse Touch' and 'Keyboard' inside Chiaki-ng settings." if inp_bad else "OK — no mouse/trackpad conflict.",
            }
        )

        hw_dec = str(ch.get("hw_decoder", "")).strip().lower()
        codec = str(ch.get("codec_local_ps5", "")).strip().upper()
        hw_ok = hw_dec not in ("", "none", "software", "false")
        items.append(
            {
                "id": "hw_decoder",
                "status": "green" if hw_ok else "yellow",
                "title_pt": "Decodificação de Vídeo por Hardware",
                "title_en": "Hardware Video Decoding",
                "detail_pt": f"Decoder={hw_dec or 'nenhum'} | Codec={codec or 'N/A'}",
                "detail_en": f"Decoder={hw_dec or 'none'} | Codec={codec or 'N/A'}",
                "tip_pt": "OK — decodificação acelerada por GPU." if hw_ok else "Ative 'd3d11va' ou 'vulkan' em Chiaki-ng -> Video para menos atraso.",
                "tip_en": "OK — GPU-accelerated decoding active." if hw_ok else "Enable 'd3d11va' or 'vulkan' in Chiaki-ng -> Video for lower latency.",
            }
        )

    return items


# ===========================================================================
# Conversor Radial 2D Mouse/Trackpad -> Analógico Direito (RX/RY)
# ===========================================================================

class MouseToStickConverter:
    """Converte deltas (dx, dy) em vetor radial 2D (RX, RY) suave e sem drift."""

    def __init__(self, config: GtaConfig) -> None:
        self.config = config
        self._rx: float = 0.0
        self._ry: float = 0.0
        self._idle_seconds: float = 0.0

    def reset(self) -> None:
        self._rx = 0.0
        self._ry = 0.0
        self._idle_seconds = 0.0

    def update(
        self,
        dx: float,
        dy: float,
        dt: float,
        aiming_ads: bool = False,
    ) -> tuple[float, float]:
        """Calcula `(rx, ry)` em `[-1.0, +1.0]` com deadzone radial e retorno ao centro."""
        eff_dy = -dy if self.config.inverter_y_mouse else dy
        raw_mag = math.hypot(dx, eff_dy)

        if raw_mag <= self.config.mouse_deadzone_entrada:
            self._idle_seconds += max(0.0, dt)
            timeout_s = max(0.0, self.config.mouse_timeout_centro_ms / 1000.0)
            if self._idle_seconds >= timeout_s:
                self._rx = 0.0
                self._ry = 0.0
                return (0.0, 0.0)
            decay = clamp_float(self.config.mouse_decaimento_parada, 0.0, 0.90)
            self._rx *= decay
            self._ry *= decay
            if math.hypot(self._rx, self._ry) < 0.015:
                self._rx = 0.0
                self._ry = 0.0
            return (self._rx, self._ry)

        self._idle_seconds = 0.0
        # Usa velocidade baseada no tempo real desde o último pacote de movimento
        # (evita penalizar trackpads 60-80 Hz quando o loop roda a 200 Hz).
        dt_clamped = max(0.005, min(0.025, dt))
        norm_factor = 0.012 / dt_clamped
        ads_mult = self.config.mouse_mult_ads if aiming_ads else 1.0

        sx = dx * norm_factor * max(0.0001, self.config.mouse_sens_x) * ads_mult
        sy = eff_dy * norm_factor * max(0.0001, self.config.mouse_sens_y) * ads_mult
        mag = math.hypot(sx, sy)

        if mag > 0.0:
            ux = sx / mag
            uy = sy / mag
            clamped_mag = min(1.0, mag)
            expo = max(0.2, min(3.0, self.config.mouse_curva_expo))
            curved = math.pow(clamped_mag, expo)
            dz = clamp_float(self.config.mouse_deadzone_compensacao, 0.0, 0.45)
            out_mag = min(1.0, dz + curved * (1.0 - dz))
            target_x = ux * out_mag
            target_y = uy * out_mag
        else:
            target_x = 0.0
            target_y = 0.0

        alpha = 1.0 - clamp_float(self.config.mouse_suavizacao, 0.0, 0.85)
        self._rx = self._rx + alpha * (target_x - self._rx)
        self._ry = self._ry + alpha * (target_y - self._ry)
        return (
            clamp_float(self._rx, -1.0, 1.0),
            clamp_float(self._ry, -1.0, 1.0),
        )


# ===========================================================================
# Máquina de Estados Exclusiva (dirigida por mapeamento.json)
# ===========================================================================

@dataclass
class InputSnapshot:
    """Fotografia das teclas, cliques e deltas do mouse/trackpad no quadro."""

    pressed_keys: set[int] = field(default_factory=set)
    mouse_dx: float = 0.0
    mouse_dy: float = 0.0


class GtaStateMachine:
    """Executa o mapeamento de `GtaConfig` garantindo exclusividade total entre modos."""

    def __init__(self, config: GtaConfig | None = None) -> None:
        self.config = config if config is not None else GtaConfig()
        self.modo_atual: str = "modo_a_pe"  # modo_a_pe | modo_veiculo | modo_voo
        self.ativo: bool = self.config.iniciar_ativo

        self._mouse_conv = MouseToStickConverter(self.config)
        self._r2_rampa: float = 0.0
        self._l2_rampa: float = 0.0
        self._lx_rampa: float = 0.0
        self._l2_toggled: bool = False
        self._cross_tap_start: float | None = None
        self._touchpad_until: float = 0.0
        self._triangle_until: float = 0.0
        self._toast_text: str = ""
        self._toast_until: float = 0.0

        self._prev_keys: set[int] = set()
        self._blocked_until_release: set[int] = set()
        self._dual_keys = self.config.dual_context_keys()
        self._last_mode_event: str = "Inicializado em MODO A PÉ"

    @property
    def modo_veiculo(self) -> bool:
        return self.modo_atual == "modo_veiculo"

    @modo_veiculo.setter
    def modo_veiculo(self, value: bool) -> None:
        self.modo_atual = "modo_veiculo" if value else "modo_a_pe"

    @property
    def modo_voo(self) -> bool:
        return self.modo_atual == "modo_voo"

    @property
    def modo_nome(self) -> str:
        return {
            "modo_a_pe": "MODO A PÉ",
            "modo_veiculo": "MODO VEÍCULO",
            "modo_voo": "MODO VOO",
        }.get(self.modo_atual, "MODO A PÉ")

    @property
    def last_mode_event(self) -> str:
        return self._last_mode_event

    def get_active_toast(self, now: float | None = None) -> str:
        ts = time.perf_counter() if now is None else now
        return self._toast_text if ts < self._toast_until else ""

    def _set_toast(self, text: str, duration_s: float = 2.2, now: float | None = None) -> None:
        ts = time.perf_counter() if now is None else now
        self._toast_text = text
        self._toast_until = ts + duration_s

    def _current_mode_map(self) -> dict[int, str]:
        if self.modo_atual == "modo_veiculo":
            return self.config.modo_veiculo
        if self.modo_atual == "modo_voo":
            return self.config.modo_voo
        return self.config.modo_a_pe

    def _reset_mode_exclusive_state(self, current_pressed: set[int]) -> None:
        self._r2_rampa = 0.0
        self._l2_rampa = 0.0
        self._lx_rampa = 0.0
        self._l2_toggled = False
        self._cross_tap_start = None
        if self.config.exigir_soltar_ao_trocar_modo:
            self._blocked_until_release = current_pressed.intersection(self._dual_keys)
        else:
            self._blocked_until_release.clear()

    def toggle_mode(self, current_pressed: set[int] | None = None, source: str = "manual", now: float | None = None) -> bool:
        pressed = current_pressed if current_pressed is not None else set()
        self.modo_atual = "modo_veiculo" if self.modo_atual == "modo_a_pe" else "modo_a_pe"
        self._reset_mode_exclusive_state(pressed)
        self._last_mode_event = f"Alternado para {self.modo_nome} ({source})"
        self._set_toast(self.modo_nome, duration_s=1.8, now=now)
        _beep_async(
            ((880, 55),) if self.modo_veiculo else ((440, 55),),
            self.config.feedback_sonoro,
        )
        return self.modo_veiculo

    def toggle_flight_mode(self, current_pressed: set[int] | None = None, source: str = "tecla F2", now: float | None = None) -> bool:
        pressed = current_pressed if current_pressed is not None else set()
        self.modo_atual = "modo_a_pe" if self.modo_atual == "modo_voo" else "modo_voo"
        self._reset_mode_exclusive_state(pressed)
        self._last_mode_event = f"Alternado para {self.modo_nome} ({source})"
        self._set_toast(f"{self.modo_nome} ATIVO" if self.modo_voo else "MODO A PÉ", duration_s=2.0, now=now)
        _beep_async(
            ((660, 40), (880, 40), (1100, 55)) if self.modo_voo else ((880, 40), (440, 55)),
            self.config.feedback_sonoro,
        )
        return self.modo_voo

    def adjust_sensitivity(self, factor: float, now: float | None = None) -> tuple[float, float]:
        self.config.mouse_sens_x = round(clamp_float(self.config.mouse_sens_x * factor, 0.02, 0.80), 3)
        self.config.mouse_sens_y = round(clamp_float(self.config.mouse_sens_y * factor, 0.02, 0.80), 3)
        msg = f"Sensibilidade: X={self.config.mouse_sens_x:.2f} | Y={self.config.mouse_sens_y:.2f}"
        self._last_mode_event = msg
        self._set_toast(msg, duration_s=2.4, now=now)
        _beep_async(((780, 30),) if factor > 1.0 else ((520, 30),), self.config.feedback_sonoro)
        return (self.config.mouse_sens_x, self.config.mouse_sens_y)

    def toggle_active(self) -> bool:
        self.ativo = not self.ativo
        self.reset_runtime()
        estado = "ATIVADO" if self.ativo else "PAUSADO"
        self._last_mode_event = f"Script {estado} via {CODE_TO_KEY_NAME.get(self.config.vk_toggle_pause, 'F12')}"
        _beep_async(
            ((660, 45), (990, 60)) if self.ativo else ((660, 45), (330, 60)),
            self.config.feedback_sonoro,
        )
        return self.ativo

    def reset_runtime(self) -> None:
        self._mouse_conv.reset()
        self._r2_rampa = 0.0
        self._l2_rampa = 0.0
        self._lx_rampa = 0.0
        self._l2_toggled = False
        self._cross_tap_start = None
        self._touchpad_until = 0.0
        self._triangle_until = 0.0
        self._blocked_until_release.clear()
        self._prev_keys.clear()

    def _collect_actions(
        self,
        keys: set[int],
        ctx_keys: set[int],
    ) -> set[str]:
        """Resolve ações com PRIORIDADE DE MODO sobre Globais (zero vazamento!)."""
        active_actions: set[str] = set()
        mode_map = self._current_mode_map()

        for code, act in mode_map.items():
            if code in ctx_keys:
                active_actions.add(act)

        for code, act in self.config.globais.items():
            if code in mode_map:
                continue
            if code in keys:
                active_actions.add(act)

        return active_actions

    def _collect_just_pressed_actions(self, just_pressed: set[int]) -> set[str]:
        acts: set[str] = set()
        mode_map = self._current_mode_map()
        for code in just_pressed:
            if code in mode_map:
                acts.add(mode_map[code])
            elif code in self.config.globais:
                acts.add(self.config.globais[code])
        return acts

    def step(
        self,
        snapshot: InputSnapshot,
        dt: float,
        now: float | None = None,
    ) -> ControllerState:
        ts = time.perf_counter() if now is None else now
        keys = set(snapshot.pressed_keys)
        just_pressed = keys - self._prev_keys

        # Pausa/Ativa (F12 padrão)
        if self.config.vk_toggle_pause in just_pressed:
            self.toggle_active()
            self._prev_keys = keys
            return ControllerState()

        if not self.ativo:
            self._prev_keys = keys
            return ControllerState()

        if self._blocked_until_release:
            self._blocked_until_release.intersection_update(keys)

        # Hotkeys ao vivo de sensibilidade (F7 -10% / F8 +10%)
        if self.config.vk_sens_down in just_pressed:
            self.adjust_sensitivity(0.90, now=ts)
        elif self.config.vk_sens_up in just_pressed:
            self.adjust_sensitivity(1.10, now=ts)

        # Controles de modo (F1 silencioso / F2 Voo / F com Triângulo)
        if self.config.vk_toggle_flight in just_pressed:
            self.toggle_flight_mode(current_pressed=keys, source="tecla F2", now=ts)
        elif self.config.vk_toggle_mode_silent in just_pressed:
            self.toggle_mode(current_pressed=keys, source="troca manual", now=ts)
        elif self.config.vk_toggle_mode_triangle in just_pressed:
            self.toggle_mode(current_pressed=keys, source="tecla F", now=ts)
            self._triangle_until = max(
                self._triangle_until,
                ts + (self.config.triangle_tap_f_ms / 1000.0),
            )

        ctx_keys = keys - self._blocked_until_release
        actions = self._collect_actions(keys, ctx_keys)
        just_actions = self._collect_just_pressed_actions(just_pressed)

        if "TOUCHPAD_TAP" in just_actions:
            self._touchpad_until = max(
                self._touchpad_until,
                ts + (self.config.touchpad_tap_v_ms / 1000.0),
            )
        if "TOUCHPAD_HOLD_500" in just_actions:
            self._touchpad_until = max(
                self._touchpad_until,
                ts + (self.config.touchpad_hold_m_ms / 1000.0),
            )
        if "L2_TOGGLE" in just_actions:
            self._l2_toggled = not self._l2_toggled

        state = ControllerState()

        # Botões simples
        btn_map = {
            "CROSS": PSButton.CROSS,
            "CIRCLE": PSButton.CIRCLE,
            "SQUARE": PSButton.SQUARE,
            "TRIANGLE": PSButton.TRIANGLE,
            "L1": PSButton.L1,
            "R1": PSButton.R1,
            "L3": PSButton.L3,
            "R3": PSButton.R3,
            "OPTIONS": PSButton.OPTIONS,
            "SHARE": PSButton.SHARE,
            "PS": PSButton.PS,
        }
        for act_name, ps_btn in btn_map.items():
            if act_name in actions:
                state.buttons[ps_btn] = True

        # Auto-tap de Sprint GTA (CROSS_TAP): pulsa ✕ continuamente enquanto segura
        if "CROSS_TAP" in actions:
            if self._cross_tap_start is None:
                self._cross_tap_start = ts
            half_s = max(0.025, self.config.sprint_tap_ms / 1000.0)
            phase = int((ts - self._cross_tap_start) / half_s)
            if (phase % 2) == 0:
                state.buttons[PSButton.CROSS] = True
        else:
            self._cross_tap_start = None

        if "L3+R3" in actions:
            state.buttons[PSButton.L3] = True
            state.buttons[PSButton.R3] = True

        if (self.config.vk_toggle_mode_triangle in keys) or (ts < self._triangle_until):
            state.buttons[PSButton.TRIANGLE] = True

        if ("TOUCHPAD_TAP" in actions) or ("TOUCHPAD_HOLD_500" in actions) or (ts < self._touchpad_until):
            state.buttons[PSButton.TOUCHPAD] = True

        # D-Pad com cancelamento SOCD
        up = "DPAD_UP" in actions
        down = "DPAD_DOWN" in actions
        left = "DPAD_LEFT" in actions
        right = "DPAD_RIGHT" in actions
        if up and down:
            up = down = False
        if left and right:
            left = right = False
        if up and left:
            state.dpad = DPadDirection.UP_LEFT
        elif up and right:
            state.dpad = DPadDirection.UP_RIGHT
        elif down and left:
            state.dpad = DPadDirection.DOWN_LEFT
        elif down and right:
            state.dpad = DPadDirection.DOWN_RIGHT
        elif up:
            state.dpad = DPadDirection.UP
        elif down:
            state.dpad = DPadDirection.DOWN
        elif left:
            state.dpad = DPadDirection.LEFT
        elif right:
            state.dpad = DPadDirection.RIGHT
        else:
            state.dpad = DPadDirection.NONE

        # Analógico Esquerdo (LX / LY) com rampa suave opcional de direção no Modo Veículo
        lx_neg = "LX-" in actions
        lx_pos = "LX+" in actions
        lx_target = -1.0 if (lx_neg and not lx_pos) else (1.0 if (lx_pos and not lx_neg) else 0.0)

        if self.modo_veiculo and self.config.tempo_rampa_direcao_lx_ms > 0.0:
            if lx_target == 0.0:
                # Retorno instantâneo ao centro ao soltar direção (controle preciso de contra-esterço)
                self._lx_rampa = 0.0
            else:
                # Ao inverter o lado rapidamente, cruza direto do zero sem arrastar do lado oposto
                if (self._lx_rampa * lx_target) < 0.0:
                    self._lx_rampa = 0.0
                ramp_lx_s = max(0.001, self.config.tempo_rampa_direcao_lx_ms / 1000.0)
                step_lx = max(0.0, dt) / ramp_lx_s
                if lx_target > 0.0:
                    self._lx_rampa = min(1.0, self._lx_rampa + step_lx)
                    if self._lx_rampa >= 0.9999:
                        self._lx_rampa = 1.0
                else:
                    self._lx_rampa = max(-1.0, self._lx_rampa - step_lx)
                    if self._lx_rampa <= -0.9999:
                        self._lx_rampa = -1.0
            lx_final = self._lx_rampa
        else:
            self._lx_rampa = 0.0
            lx_final = lx_target

        ly_up = "LY+" in actions
        ly_dn = "LY-" in actions
        if ly_up and not ly_dn:
            ly_val = -1.0  # DS4/SDL3: -1.0 (HID 1) = Frente / Cima
        elif ly_dn and not ly_up:
            ly_val = 1.0   # DS4/SDL3: +1.0 (HID 255) = Trás / Baixo
        else:
            ly_val = 0.0
        ly_final = -ly_val if self.config.inverter_y_esquerdo else ly_val

        # Modificador opcional de caminhada lenta / manobra fina
        if "MOD_LENTO" in actions:
            slow = clamp_float(self.config.fator_caminhada_lenta, 0.15, 0.90)
            lx_final *= slow
            ly_final *= slow

        state.lx = clamp_float(lx_final, -1.0, 1.0)
        state.ly = clamp_float(ly_final, -1.0, 1.0)

        # Gatilhos L2 / R2 (com suporte a rampa suave em ambos!)
        if "L2_RAMPA" in actions:
            ramp_l2_s = max(0.001, self.config.tempo_rampa_l2_ms / 1000.0)
            self._l2_rampa = min(1.0, self._l2_rampa + (max(0.0, dt) / ramp_l2_s))
            if self._l2_rampa >= 0.9999:
                self._l2_rampa = 1.0
        else:
            self._l2_rampa = 0.0
        l2_direct = 1.0 if (("L2" in actions) or self._l2_toggled) else 0.0
        state.l2 = max(l2_direct, self._l2_rampa)

        if "R2_RAMPA" in actions:
            ramp_s = max(0.001, self.config.tempo_rampa_r2_ms / 1000.0)
            self._r2_rampa = min(1.0, self._r2_rampa + (max(0.0, dt) / ramp_s))
            if self._r2_rampa >= 0.9999:
                self._r2_rampa = 1.0
        else:
            self._r2_rampa = 0.0

        r2_direct = 1.0 if ("R2" in actions) else 0.0
        state.r2 = max(r2_direct, self._r2_rampa)

        state.buttons[PSButton.L2] = state.l2 >= 0.05
        state.buttons[PSButton.R2] = state.r2 >= 0.05

        # Analógico Direito (RX / RY) — Mouse e Trackpad
        aiming_ads = (self.modo_atual == "modo_a_pe") and (state.l2 >= 0.5)
        rx, ry = self._mouse_conv.update(
            snapshot.mouse_dx, snapshot.mouse_dy, dt, aiming_ads=aiming_ads
        )
        state.rx = rx
        state.ry = ry

        self._prev_keys = keys
        return state


# ===========================================================================
# Captura Win32 Híbrida: Raw Input (Mouse) + Cursor Delta (Trackpad) + Hooks
# ===========================================================================

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

WM_QUIT = 0x0012
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
LLKHF_ALTDOWN = 0x20

RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_INPUTSINK = 0x00000100
MOUSE_MOVE_ABSOLUTE = 0x0001
HWND_MESSAGE = ctypes.c_void_p(-3)


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", WPARAM),
    ]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("ulButtons", wintypes.ULONG),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class _RAWINPUT_MOUSE_PACKET(ctypes.Structure):
    _fields_ = [
        ("header", _RAWINPUTHEADER),
        ("mouse", _RAWMOUSE),
    ]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def get_foreground_window_info() -> tuple[int, str, str]:
    """Retorna `(hwnd, nome_executavel_minusculo, titulo_janela)` da janela em foco."""
    if platform.system() != "Windows":
        return 0, "", ""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""

    title_buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title_buf, 256)
    title = title_buf.value.strip()

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return int(hwnd), "", title

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_proc:
        return int(hwnd), "", title
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
            return int(hwnd), Path(buf.value).name.lower(), title
        return int(hwnd), "", title
    finally:
        kernel32.CloseHandle(h_proc)


class CursorCenterTracker:
    """Captura deltas de Trackpad de notebook sem destruir acumulação sub-pixel.

    Diferente de chamar SetCursorPos a cada quadro (que zera movimentos lentos
    de Precision Touchpads do Windows 11), acompanha o ponteiro livremente
    dentro de uma zona segura (ex.: 320x320 px no centro da janela) e só
    recentraliza quando o cursor se aproxima do limite dessa zona.
    """

    def __init__(self, free_radius_px: int = 160) -> None:
        self._free_radius = max(60, int(free_radius_px))
        self._active: bool = False
        self._center: tuple[int, int] | None = None
        self._prev_pt: tuple[int, int] | None = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True) if platform.system() == "Windows" else None
        if self._user32 is not None:
            self._user32.GetForegroundWindow.restype = wintypes.HWND
            self._user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            self._user32.GetWindowRect.restype = wintypes.BOOL
            self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            self._user32.GetCursorPos.restype = wintypes.BOOL
            self._user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            self._user32.SetCursorPos.restype = wintypes.BOOL

    def reset(self) -> None:
        self._active = False
        self._center = None
        self._prev_pt = None

    def _compute_bounds(self) -> tuple[int, int, int, int] | None:
        if self._user32 is None:
            return None
        hwnd = self._user32.GetForegroundWindow()
        rect = wintypes.RECT()
        if hwnd and self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 120 and h > 120:
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                rx = min(self._free_radius, max(40, w // 3))
                ry = min(self._free_radius, max(40, h // 3))
                return (cx, cy, rx, ry)
        return None

    def poll_delta_and_recenter(self) -> tuple[float, float]:
        """Lê delta real `(pt - prev_pt)` preservando sub-pixel e só recentraliza na borda."""
        if self._user32 is None:
            return (0.0, 0.0)
        bounds = self._compute_bounds()
        if bounds is None:
            self.reset()
            return (0.0, 0.0)

        cx, cy, rx, ry = bounds
        pt = wintypes.POINT()
        if not self._user32.GetCursorPos(ctypes.byref(pt)):
            return (0.0, 0.0)

        if (not self._active) or (self._center != (cx, cy)) or (self._prev_pt is None):
            self._user32.SetCursorPos(cx, cy)
            self._center = (cx, cy)
            self._prev_pt = (cx, cy)
            self._active = True
            return (0.0, 0.0)

        px, py = self._prev_pt
        dx = float(pt.x - px)
        dy = float(pt.y - py)

        # Só chama SetCursorPos quando o cursor se aproxima do limite da caixa livre
        if abs(pt.x - cx) >= rx or abs(pt.y - cy) >= ry:
            self._user32.SetCursorPos(cx, cy)
            self._prev_pt = (cx, cy)
        else:
            self._prev_pt = (pt.x, pt.y)

        return (dx, dy)


def _enable_windows_ansi_console() -> bool:
    if platform.system() != "Windows" or sys.stdout is None or not sys.stdout.isatty():
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11)
        mode = wintypes.DWORD(0)
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return False


class Win32InputCapture:
    """Thread dedicada de captura Win32 (Raw Input Mouse + Hooks Teclado/Mouse/Scroll)."""

    def __init__(
        self,
        managed_vks: frozenset[int],
        pause_vk: int,
        should_capture: Callable[[], bool],
        should_suppress: Callable[[], bool],
        managed_mouse_codes: frozenset[int] | None = None,
        scroll_pulso_ms: float = 75.0,
    ) -> None:
        self._managed_vks = managed_vks
        self._managed_mouse = (
            managed_mouse_codes
            if managed_mouse_codes is not None
            else frozenset({MOUSE_LEFT, MOUSE_RIGHT})
        )
        self._scroll_pulso_s = max(0.02, float(scroll_pulso_ms) / 1000.0)
        self._pause_vk = pause_vk
        self._should_capture = should_capture
        self._should_suppress = should_suppress

        self._lock = threading.Lock()
        self._pressed: set[int] = set()
        self._scroll_until: dict[int, float] = {}
        self._suppressed_vks: set[int] = set()
        self._suppressed_mouse: set[int] = set()
        self._dx: float = 0.0
        self._dy: float = 0.0
        self._raw_mouse_packets: int = 0
        self._last_abs_xy: tuple[int, int] | None = None
        self._pause_physical_down: bool = False
        self._pause_pending_edges: int = 0

        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._win_thread_id: int = 0
        self._start_error: Exception | None = None

        self._wndproc_ref: Any = None
        self._kb_hook_ref: Any = None
        self._ms_hook_ref: Any = None

    def start(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Win32InputCapture requer Windows.")
        self._ready_event.clear()
        self._start_error = None
        self._thread = threading.Thread(
            target=self._message_loop_thread,
            name="Win32RawInputHooks",
            daemon=False,
        )
        self._thread.start()
        self._ready_event.wait(timeout=3.0)
        if self._start_error is not None:
            raise self._start_error

    def stop(self) -> None:
        tid = self._win_thread_id
        if tid:
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW.argtypes = [
                    wintypes.DWORD,
                    wintypes.UINT,
                    WPARAM,
                    LPARAM,
                ]
                user32.PostThreadMessageW.restype = wintypes.BOOL
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def clear_buffers(self) -> None:
        with self._lock:
            self._pressed.clear()
            self._scroll_until.clear()
            self._dx = 0.0
            self._dy = 0.0
            self._last_abs_xy = None

    def get_raw_mouse_packet_count(self) -> int:
        with self._lock:
            return self._raw_mouse_packets

    def consume_snapshot(self) -> tuple[InputSnapshot, bool]:
        now = time.perf_counter()
        with self._lock:
            pause_triggered = self._pause_pending_edges > 0
            self._pause_pending_edges = 0
            active_keys = set(self._pressed)
            # Inclui pulsos curtos de Scroll Up/Down ainda ativos
            expired = [k for k, until in self._scroll_until.items() if now >= until]
            for k in expired:
                del self._scroll_until[k]
            active_keys.update(self._scroll_until.keys())

            snap = InputSnapshot(
                pressed_keys=active_keys,
                mouse_dx=self._dx,
                mouse_dy=self._dy,
            )
            self._dx = 0.0
            self._dy = 0.0
        return snap, pause_triggered

    def _message_loop_thread(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.RegisterRawInputDevices.argtypes = [
            ctypes.POINTER(_RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT,
        ]
        user32.RegisterRawInputDevices.restype = wintypes.BOOL
        user32.GetRawInputData.argtypes = [
            ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
            ctypes.POINTER(wintypes.UINT), wintypes.UINT,
        ]
        user32.GetRawInputData.restype = wintypes.UINT
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, WPARAM, LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT

        self._win_thread_id = int(kernel32.GetCurrentThreadId())
        h_instance = kernel32.GetModuleHandleW(None)
        class_name = f"GtaRawInputHiddenWnd_{self._win_thread_id}"

        raw_packet = _RAWINPUT_MOUSE_PACKET()
        header_size = ctypes.sizeof(_RAWINPUTHEADER)
        packet_size = ctypes.sizeof(_RAWINPUT_MOUSE_PACKET)

        def _wnd_proc(hwnd: Any, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_INPUT:
                if self._should_capture():
                    cb_size = wintypes.UINT(packet_size)
                    res = user32.GetRawInputData(
                        ctypes.c_void_p(lparam),
                        RID_INPUT,
                        ctypes.byref(raw_packet),
                        ctypes.byref(cb_size),
                        header_size,
                    )
                    if res != 0xFFFFFFFF and raw_packet.header.dwType == RIM_TYPEMOUSE:
                        is_abs = bool(raw_packet.mouse.usFlags & MOUSE_MOVE_ABSOLUTE)
                        lx = int(raw_packet.mouse.lLastX)
                        ly = int(raw_packet.mouse.lLastY)
                        if not is_abs:
                            dx, dy = lx, ly
                        else:
                            with self._lock:
                                prev = self._last_abs_xy
                                self._last_abs_xy = (lx, ly)
                            dx = round((lx - prev[0]) / 24.0) if prev is not None else 0
                            dy = round((ly - prev[1]) / 24.0) if prev is not None else 0
                        if dx or dy:
                            with self._lock:
                                self._dx += float(dx)
                                self._dy += float(dy)
                                self._raw_mouse_packets += 1
                return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))
            return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))

        def _kb_hook(n_code: int, wparam: int, lparam: int) -> int:
            if n_code >= 0:
                kbd = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(kbd.vkCode)
                is_down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = wparam in (WM_KEYUP, WM_SYSKEYUP)
                alt_down = bool(kbd.flags & LLKHF_ALTDOWN)

                if vk == self._pause_vk:
                    with self._lock:
                        if is_down and (not self._pause_physical_down):
                            self._pause_physical_down = True
                            self._pause_pending_edges += 1
                        elif is_up:
                            self._pause_physical_down = False
                    return 1

                # Nunca bloqueia Alt+Tab / Alt+F4 / Alt+Esc para preservar saída de emergência
                if alt_down and vk in (0x09, 0x73, 0x1B):
                    return int(user32.CallNextHookEx(None, n_code, wparam, lparam))

                if vk in self._managed_vks:
                    capture = self._should_capture()
                    # Nunca suprime LALT (0xA4) no Windows para que Alt+Tab sempre funcione
                    suppress = self._should_suppress() and (vk != 0xA4)
                    with self._lock:
                        if is_down:
                            if capture:
                                self._pressed.add(vk)
                                if suppress:
                                    self._suppressed_vks.add(vk)
                                    return 1
                        elif is_up:
                            self._pressed.discard(vk)
                            if vk in self._suppressed_vks:
                                self._suppressed_vks.discard(vk)
                                return 1
            return int(user32.CallNextHookEx(None, n_code, wparam, lparam))

        def _ms_hook(n_code: int, wparam: int, lparam: int) -> int:
            if n_code >= 0:
                btn: int | None = None
                is_down = False
                is_scroll = False

                if wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    btn = MOUSE_LEFT
                    is_down = wparam == WM_LBUTTONDOWN
                elif wparam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                    btn = MOUSE_RIGHT
                    is_down = wparam == WM_RBUTTONDOWN
                elif wparam in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                    btn = MOUSE_MIDDLE
                    is_down = wparam == WM_MBUTTONDOWN
                elif wparam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                    ms = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                    xbtn = (int(ms.mouseData) >> 16) & 0xFFFF
                    if xbtn == XBUTTON1:
                        btn = MOUSE_X1
                    elif xbtn == XBUTTON2:
                        btn = MOUSE_X2
                    is_down = wparam == WM_XBUTTONDOWN
                elif wparam == WM_MOUSEWHEEL:
                    ms = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                    wheel_delta = ctypes.c_short((int(ms.mouseData) >> 16) & 0xFFFF).value
                    if wheel_delta > 0:
                        btn = SCROLL_UP
                        is_scroll = True
                    elif wheel_delta < 0:
                        btn = SCROLL_DOWN
                        is_scroll = True

                if btn is not None and btn in self._managed_mouse:
                    capture = self._should_capture()
                    suppress = self._should_suppress()
                    if is_scroll:
                        if capture:
                            with self._lock:
                                self._scroll_until[btn] = time.perf_counter() + self._scroll_pulso_s
                            if suppress:
                                return 1
                    else:
                        with self._lock:
                            if is_down:
                                if capture:
                                    self._pressed.add(btn)
                                    if suppress:
                                        self._suppressed_mouse.add(btn)
                                        return 1
                            else:
                                self._pressed.discard(btn)
                                if btn in self._suppressed_mouse:
                                    self._suppressed_mouse.discard(btn)
                                    return 1
            return int(user32.CallNextHookEx(None, n_code, wparam, lparam))

        self._wndproc_ref = WNDPROC(_wnd_proc)
        self._kb_hook_ref = HOOKPROC(_kb_hook)
        self._ms_hook_ref = HOOKPROC(_ms_hook)

        hwnd = None
        kb_hook = None
        ms_hook = None
        atom = 0
        try:
            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = h_instance
            wc.lpszClassName = class_name
            atom = user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                raise OSError(f"RegisterClassExW falhou (Win32={ctypes.get_last_error()})")

            hwnd = user32.CreateWindowExW(
                0, class_name, class_name, 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, h_instance, None,
            )
            if not hwnd:
                raise OSError(f"CreateWindowExW falhou (Win32={ctypes.get_last_error()})")

            rid = _RAWINPUTDEVICE(
                usUsagePage=0x01, usUsage=0x02, dwFlags=RIDEV_INPUTSINK, hwndTarget=hwnd,
            )
            if not user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(_RAWINPUTDEVICE)
            ):
                raise OSError(f"RegisterRawInputDevices falhou (Win32={ctypes.get_last_error()})")

            kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_hook_ref, None, 0)
            if not kb_hook:
                raise OSError(f"SetWindowsHookExW(KB) falhou (Win32={ctypes.get_last_error()})")

            ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_hook_ref, None, 0)
            if not ms_hook:
                raise OSError(f"SetWindowsHookExW(MS) falhou (Win32={ctypes.get_last_error()})")

            self._ready_event.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._start_error = exc
            self._ready_event.set()
        finally:
            if kb_hook:
                user32.UnhookWindowsHookEx(kb_hook)
            if ms_hook:
                user32.UnhookWindowsHookEx(ms_hook)
            if hwnd:
                user32.DestroyWindow(hwnd)
            if atom:
                user32.UnregisterClassW(class_name, h_instance)
            self._win_thread_id = 0


# ===========================================================================
# Aplicação principal
# ===========================================================================

class SingleInstanceError(RuntimeError):
    """Levantada quando já existe outra instância ativa do controle virtual."""


class _SingleInstanceMutex:
    """Garante via Mutex nomeado do Windows que apenas 1 controle virtual rode por vez."""

    MUTEX_NAME = r"Local\GTA_PS5_Controle_Virtual_SingleInstance_v1"

    def __init__(self) -> None:
        self._handle: Any = None
        self._kernel32: Any = None

    def acquire(self) -> None:
        if platform.system() != "Windows":
            return
        ERROR_ALREADY_EXISTS = 183
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

        handle = k32.CreateMutexW(None, False, self.MUTEX_NAME)
        err = ctypes.get_last_error()
        if handle and err == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            raise SingleInstanceError(
                "Já existe outra instância do controle virtual (GTA_PS5_Controle.exe ou "
                "gta_controle.py) em execução! Feche a instância anterior antes de iniciar "
                "para evitar dois controles disputando o teclado e o trackpad."
            )
        self._handle = handle
        self._kernel32 = k32

    def release(self) -> None:
        if self._handle and self._kernel32 is not None:
            try:
                self._kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


class GtaKeyboardMouseApp:
    """Orquestra captura híbrida (Mouse + Trackpad), máquina de estados e DS4."""

    def __init__(
        self,
        config: GtaConfig,
        mapping_path: Path,
        simulate: bool = False,
        headless: bool = False,
    ) -> None:
        self.config = config
        self.mapping_path = mapping_path
        self.simulate = simulate
        self.headless = headless
        self.sm = GtaStateMachine(config)

        backend = SimulatedDS4Backend() if simulate else VDS4ViGEmBackend()
        ctrl_cfg = ControllerConfig(
            update_rate_hz=config.taxa_atualizacao_hz,
            invert_y_axis=False,
        )
        # auto_loop=False: elimina double-buffering (envio direto imediato a 200 Hz!)
        self.controller = VirtualPlayStationController(
            backend=backend,
            config=ctrl_cfg,
            auto_loop=False,
        )

        self._chiaki_focused: bool = not config.apenas_chiaki_em_foco
        self._last_hwnd: int = -1
        self._last_proc_name: str = ""
        self._last_focus_check: float = 0.0
        self._user32_focus = ctypes.WinDLL("user32", use_last_error=True) if platform.system() == "Windows" else None
        if self._user32_focus is not None:
            self._user32_focus.GetForegroundWindow.restype = wintypes.HWND
        self._ansi_enabled: bool = False if headless else _enable_windows_ansi_console()
        self._hud_initialized: bool = False
        self._last_dx_dy: tuple[float, float] = (0.0, 0.0)
        self._mouse_source: str = "AGUARDANDO"
        self._trackpad_packets: int = 0
        self._chiaki_keymap_warnings: list[str] = check_chiaki_conflicting_keymap()
        self._uipi_warning: str | None = check_chiaki_uipi_mismatch()
        self._cursor_tracker = CursorCenterTracker()
        self._mutex = _SingleInstanceMutex()
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self._bg_error: str | None = None
        self._running: bool = False

        self.capture = Win32InputCapture(
            managed_vks=config.all_managed_keyboard_vks(),
            pause_vk=config.vk_toggle_pause,
            should_capture=self._is_capturing_enabled,
            should_suppress=self._is_suppression_enabled,
            managed_mouse_codes=config.all_managed_mouse_codes(),
            scroll_pulso_ms=config.scroll_pulso_ms,
        )

    @property
    def is_running(self) -> bool:
        return self._running and self.controller.is_running

    def get_live_status(self) -> dict[str, Any]:
        """Snapshot leve de telemetria + botões/eixos ao vivo para GUI e Mini-Overlay."""
        metrics = self.controller.get_metrics()
        st = self.controller.get_state()
        pressed_buttons = frozenset(btn.name for btn, active in st.buttons.items() if active)
        return {
            "running": self.is_running,
            "ativo": self.sm.ativo,
            "modo_atual": self.sm.modo_atual,
            "modo_veiculo": self.sm.modo_veiculo,
            "modo_voo": self.sm.modo_voo,
            "modo_nome": self.sm.modo_nome,
            "chiaki_focused": self._chiaki_focused,
            "apenas_chiaki": self.config.apenas_chiaki_em_foco,
            "proc_name": self._last_proc_name or "N/A",
            "mouse_source": self._mouse_source,
            "raw_packets": self.capture.get_raw_mouse_packet_count(),
            "trackpad_packets": self._trackpad_packets,
            "actual_hz": metrics.actual_hz,
            "last_event": self.sm.last_mode_event,
            "toast": self.sm.get_active_toast(),
            "uipi_warning": self._uipi_warning,
            "error": self._bg_error,
            "sens_x": self.config.mouse_sens_x,
            "sens_y": self.config.mouse_sens_y,
            "l2_toggled": self.sm._l2_toggled,
            "buttons": pressed_buttons,
            "dpad": st.dpad.name,
            "lx": st.lx,
            "ly": st.ly,
            "rx": st.rx,
            "ry": st.ry,
            "l2": st.l2,
            "r2": st.r2,
        }

    def start_background(self) -> None:
        """Inicia o motor completo (DS4 virtual + Raw Input/Trackpad + Hooks) em thread."""
        if self._running:
            return
        self._mutex.acquire()
        self._stop_event.clear()
        self._bg_error = None
        try:
            self.controller.start()
            self.capture.start()
        except Exception:
            self.controller.close()
            self._mutex.release()
            raise

        self._running = True
        self._bg_thread = threading.Thread(
            target=self._run_loop_core,
            name="GtaEngineLoop",
            daemon=False,
        )
        self._bg_thread.start()

    def stop_background(self) -> None:
        """Para o motor em background e libera teclado, mouse/trackpad e DS4 com segurança."""
        self._stop_event.set()
        thread = self._bg_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.5)
        self._bg_thread = None
        self._running = False
        self._cursor_tracker.reset()
        self.capture.stop()
        self.controller.close()
        self._mutex.release()

    def _is_capturing_enabled(self) -> bool:
        return self.sm.ativo and (
            (not self.config.apenas_chiaki_em_foco) or self._chiaki_focused
        )

    def _is_suppression_enabled(self) -> bool:
        return self.config.bloquear_entradas_windows and self._is_capturing_enabled()

    def _refresh_focus_state(self, now: float) -> None:
        if not self.config.apenas_chiaki_em_foco:
            self._chiaki_focused = True
            return
        if (now - self._last_focus_check) < 0.03:
            return
        self._last_focus_check = now
        if self._user32_focus is None:
            return

        hwnd = int(self._user32_focus.GetForegroundWindow() or 0)
        if hwnd != self._last_hwnd:
            _, proc_name, title = get_foreground_window_info()
            self._last_hwnd = hwnd
            self._last_proc_name = proc_name or title
            self._chiaki_focused = is_remote_play_window(proc_name, title)

    def _print_hud(self) -> None:
        ativo_str = "ATIVO" if self.sm.ativo else "PAUSADO (pressione F12)"
        if self.config.apenas_chiaki_em_foco:
            foco_str = (
                f"CHIAKI EM FOCO ({self._last_proc_name})"
                if self._chiaki_focused
                else f"AGUARDANDO FOCO NO CHIAKI (atual: {self._last_proc_name or 'N/A'})"
            )
        else:
            foco_str = "GLOBAL (todas as janelas)"

        raw_cnt = self.capture.get_raw_mouse_packet_count()
        ldx, ldy = self._last_dx_dy
        lines = [
            "=" * 78,
            "GTA PS5 — TECLADO + MOUSE/TRACKPAD -> DUALSHOCK 4 VIRTUAL (ViGEmBus)",
            "=" * 78,
            f"MAPA   : {self.mapping_path.name} (edite no Bloco de Notas para remapear)",
            f"STATUS : {ativo_str} | CONTEXTO: [{self.sm.modo_nome}]",
            f"FOCO   : {foco_str}",
            f"EVENTO : {self.sm.last_mode_event}",
            (
                f"CAMERA : Fonte={self._mouse_source} | Raw={raw_cnt} | Trackpad={self._trackpad_packets} | "
                f"Delta=({ldx:+5.1f},{ldy:+5.1f}) | Sens=({self.config.mouse_sens_x:.3f},{self.config.mouse_sens_y:.3f})"
            ),
        ]
        if self._uipi_warning:
            lines.append(f"CRITICO: {self._uipi_warning}")
        if self._chiaki_keymap_warnings:
            lines.append(
                f"ALERTA : Chiaki-ng ainda tem {len(self._chiaki_keymap_warnings)} tecla(s) no keymap interno! "
                "Limpe o Keymap no Chiaki-ng para evitar conflito."
            )
        lines.extend(
            [
                "-" * 78,
                self.controller.format_telemetry(),
                "-" * 78,
                "ATALHOS: [F] Triângulo + Modo | [F1] Trocar Modo Manual | [F12] Pausar/Ativar",
                "         [Alt+Tab] Sempre livre | [Ctrl+C] Encerrar com segurança",
                "=" * 78,
            ]
        )

        body = "\n".join(line.ljust(78) for line in "\n".join(lines).splitlines())
        if self._ansi_enabled:
            if not self._hud_initialized:
                sys.stdout.write("\033[2J\033[H")
                self._hud_initialized = True
            else:
                sys.stdout.write("\033[H")
            sys.stdout.write(body + "\n")
            sys.stdout.flush()
        else:
            print(body, flush=True)

    def _run_loop_core(self) -> None:
        target_hz = max(60.0, min(250.0, float(self.config.taxa_atualizacao_hz)))
        period = 1.0 / target_hz
        hud_period = 0.25
        last_tick = time.perf_counter()
        last_hud = 0.0
        was_capturing = False

        # Timer de 1 ms do Windows + envio síncrono direto -> latência Python < 5 ms!
        with _WindowsHighResTimer(True):
            next_tick = time.perf_counter()
            try:
                while not self._stop_event.is_set():
                    now = time.perf_counter()
                    dt = max(0.001, min(0.05, now - last_tick))
                    last_tick = now

                    self._refresh_focus_state(now)
                    snap, pause_toggled = self.capture.consume_snapshot()

                    if pause_toggled:
                        self.sm.toggle_active()
                        self.capture.clear_buffers()
                        self._cursor_tracker.reset()
                        self.controller.push_state_now(ControllerState())
                        was_capturing = False
                        if not self.headless:
                            self._print_hud()
                            last_hud = now
                        next_tick = time.perf_counter() + period
                        self._stop_event.wait(timeout=period)
                        continue

                    capturing = self._is_capturing_enabled()

                    if capturing:
                        cur_dx, cur_dy = (0.0, 0.0)
                        if self.config.usar_captura_cursor_trackpad:
                            cur_dx, cur_dy = self._cursor_tracker.poll_delta_and_recenter()

                        if snap.mouse_dx != 0.0 or snap.mouse_dy != 0.0:
                            self._mouse_source = "RAW_MOUSE"
                            self._last_dx_dy = (snap.mouse_dx, snap.mouse_dy)
                        elif cur_dx != 0.0 or cur_dy != 0.0:
                            snap.mouse_dx = cur_dx
                            snap.mouse_dy = cur_dy
                            self._trackpad_packets += 1
                            self._mouse_source = "TRACKPAD/CURSOR"
                            self._last_dx_dy = (cur_dx, cur_dy)

                        snap.pressed_keys.discard(self.config.vk_toggle_pause)
                        new_state = self.sm.step(snap, dt=dt, now=now)
                        # Envio direto e imediato ao ViGEmBus (sem esperar outra thread!)
                        self.controller.push_state_now(new_state)
                        was_capturing = True
                    else:
                        if was_capturing:
                            self.sm.reset_runtime()
                            self.capture.clear_buffers()
                            self._cursor_tracker.reset()
                            self.controller.push_state_now(ControllerState())
                            was_capturing = False

                    if (not self.headless) and (now - last_hud) >= hud_period:
                        self._print_hud()
                        last_hud = now

                    next_tick += period
                    remaining = next_tick - time.perf_counter()
                    if remaining > 0:
                        self._stop_event.wait(timeout=remaining)
                    elif remaining < -(period * 3.0):
                        next_tick = time.perf_counter()
            except Exception as exc:
                self._bg_error = str(exc)
            finally:
                self._running = False
                self._cursor_tracker.reset()

    def run(self) -> int:
        try:
            self._mutex.acquire()
        except SingleInstanceError as exc:
            print(f"\n[AVISO] {exc}")
            return 1

        try:
            self.controller.start()
        except (DriverUnavailableError, BackendDependencyError) as exc:
            self._mutex.release()
            print(f"\n[ERRO] Não foi possível iniciar o controle virtual:\n{exc}")
            print(f"Driver recomendado: ViGEmBus {VIGEMBUS_RECOMMENDED_VERSION} ({VIGEMBUS_RELEASE_URL})")
            return 1

        try:
            self.capture.start()
        except Exception as exc:
            self.controller.close()
            self._mutex.release()
            print(f"\n[ERRO] Falha ao iniciar captura Win32: {exc}")
            return 1

        def _handle_signal(signum: int, _frame: object) -> None:
            self._stop_event.set()

        signal.signal(signal.SIGINT, signal.default_int_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handle_signal)

        self._running = True
        try:
            self._run_loop_core()
            return 0
        except KeyboardInterrupt:
            print("\nInterrupção (Ctrl+C). Liberando mouse, teclado e controle virtual...")
            return 0
        finally:
            self._running = False
            self._cursor_tracker.reset()
            self.capture.stop()
            self.controller.close()
            self._mutex.release()
            print("Encerramento seguro concluído: teclado, mouse/trackpad e DS4 virtual liberados.")


# ===========================================================================
# Autotestes (`--self-test`)
# ===========================================================================

def run_self_tests() -> bool:
    checks: list[tuple[str, bool, str]] = []

    # 1. Carregamento do arquivo editável mapeamento.json
    cfg_file = load_config_from_json(DEFAULT_MAPPING_FILE)
    checks.append(
        (
            "Carregamento e validação de mapeamento.json",
            bool(cfg_file.globais and cfg_file.modo_a_pe and cfg_file.modo_veiculo),
            f"globais={len(cfg_file.globais)}, a_pe={len(cfg_file.modo_a_pe)}, veiculo={len(cfg_file.modo_veiculo)}",
        )
    )

    # Perfil padrão determinístico para validar a máquina de estados
    # (independente de customizações que o usuário tenha feito em mapeamento.json)
    cfg = GtaConfig(feedback_sonoro=False, iniciar_ativo=True)

    # 2. Alinhamento Win32 Raw Input x64
    exp_hdr = 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16
    checks.append(
        (
            "Structs Win32 Raw Input (alinhamento x64)",
            ctypes.sizeof(_RAWINPUTHEADER) == exp_hdr and ctypes.sizeof(_RAWMOUSE) == 24,
            f"RAWINPUTHEADER={ctypes.sizeof(_RAWINPUTHEADER)}B, RAWMOUSE={ctypes.sizeof(_RAWMOUSE)}B",
        )
    )

    # 3. Modo A Pé completo + câmera radial 2D (mouse/trackpad) + ADS
    sm = GtaStateMachine(cfg)
    t = 10.0
    st = sm.step(
        InputSnapshot(
            pressed_keys={
                KEY_NAME_TO_CODE["W"],
                KEY_NAME_TO_CODE["A"],
                KEY_NAME_TO_CODE["LSHIFT"],
                KEY_NAME_TO_CODE["SPACE"],
                KEY_NAME_TO_CODE["LCTRL"],
                KEY_NAME_TO_CODE["Q"],
                KEY_NAME_TO_CODE["R"],
                MOUSE_RIGHT,
                MOUSE_LEFT,
                KEY_NAME_TO_CODE["TAB"],
                KEY_NAME_TO_CODE["C"],
                KEY_NAME_TO_CODE["P"],
                KEY_NAME_TO_CODE["E"],
            },
            mouse_dx=12.0,
            mouse_dy=-10.0,
        ),
        dt=0.01,
        now=t,
    )
    foot_ok = (
        (not sm.modo_veiculo)
        and st.lx == -1.0
        and st.ly == -1.0
        and st.rx > 0.2
        and st.ry < -0.2
        and st.buttons[PSButton.CROSS]
        and st.buttons[PSButton.SQUARE]
        and st.buttons[PSButton.L3]
        and st.buttons[PSButton.R1]
        and st.buttons[PSButton.CIRCLE]
        and st.l2 == 1.0
        and st.r2 == 1.0
        and st.buttons[PSButton.L1]
        and st.buttons[PSButton.R3]
        and st.buttons[PSButton.OPTIONS]
        and st.dpad == DPadDirection.RIGHT
    )
    checks.append(
        (
            "Modo A Pé (WASD -> LX/LY, botões, mouse/trackpad -> RX/RY radial)",
            foot_ok,
            f"lx={st.lx}, ly={st.ly}, rx={st.rx:.2f}, ry={st.ry:.2f}, l2={st.l2}, r2={st.r2}",
        )
    )

    # 4. Retorno exato de RX/RY ao centro quando ponteiro/trackpad para
    st_stop = st
    for _ in range(5):
        t += 0.01
        st_stop = sm.step(InputSnapshot(pressed_keys=set(), mouse_dx=0.0, mouse_dy=0.0), dt=0.01, now=t)
    raw_stop = st_stop.to_ds4_raw_report()
    checks.append(
        (
            "Mouse/Trackpad parado -> retorno exato de RX/RY ao centro (0.00 / HID 128)",
            st_stop.rx == 0.0 and st_stop.ry == 0.0 and raw_stop.thumb_rx == 128 and raw_stop.thumb_ry == 128,
            f"rx={st_stop.rx:.2f} (HID={raw_stop.thumb_rx}), ry={st_stop.ry:.2f} (HID={raw_stop.thumb_ry})",
        )
    )

    # 5. Transição F (Triângulo + modo_veiculo=True + zero sobreposição)
    t += 0.01
    st_f = sm.step(
        InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["W"], KEY_NAME_TO_CODE["SPACE"], KEY_NAME_TO_CODE["F"]}),
        dt=0.01,
        now=t,
    )
    checks.append(
        (
            "Transição F (Triângulo + modo_veiculo=True + zero sobreposição)",
            sm.modo_veiculo and st_f.buttons[PSButton.TRIANGLE] and st_f.ly == 0.0 and st_f.r2 == 0.0 and not st_f.buttons[PSButton.SQUARE],
            f"modo_veiculo={sm.modo_veiculo}, tri={st_f.buttons[PSButton.TRIANGLE]}, ly={st_f.ly}, r2={st_f.r2}",
        )
    )

    t += 0.12
    sm.step(InputSnapshot(pressed_keys=set()), dt=0.01, now=t)

    # 6. Modo Veículo + Rampa R2 ~100 ms + exclusividade
    r2_samples: list[float] = []
    for _ in range(10):
        t += 0.01
        st_v = sm.step(
            InputSnapshot(
                pressed_keys={
                    KEY_NAME_TO_CODE["W"],
                    KEY_NAME_TO_CODE["S"],
                    KEY_NAME_TO_CODE["SPACE"],
                    KEY_NAME_TO_CODE["LCTRL"],
                    KEY_NAME_TO_CODE["Q"],
                    KEY_NAME_TO_CODE["E"],
                    MOUSE_LEFT,
                }
            ),
            dt=0.01,
            now=t,
        )
        r2_samples.append(round(st_v.r2, 2))
    checks.append(
        (
            "Modo Veículo + Rampa R2 100ms + Buzina E(L3) + Exclusividade",
            r2_samples[0] == 0.10 and r2_samples[-1] == 1.00 and st_v.l2 == 1.0 and st_v.ly == 1.0
            and st_v.buttons[PSButton.R1] and st_v.buttons[PSButton.L1] and st_v.buttons[PSButton.CIRCLE]
            and st_v.buttons[PSButton.L3] and not st_v.buttons[PSButton.CROSS] and not st_v.buttons[PSButton.SQUARE],
            f"rampa={r2_samples}, L1={st_v.buttons[PSButton.L1]}, R1={st_v.buttons[PSButton.R1]}, L3={st_v.buttons[PSButton.L3]}",
        )
    )

    # 7. F1 alterna silenciosamente e M segura Touchpad 500 ms
    t += 0.01
    st_f1 = sm.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["F1"]}), dt=0.01, now=t)
    t += 0.02
    st_m0 = sm.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["M"]}), dt=0.01, now=t)
    t += 0.30
    st_m300 = sm.step(InputSnapshot(pressed_keys=set()), dt=0.01, now=t)
    t += 0.25
    st_m550 = sm.step(InputSnapshot(pressed_keys=set()), dt=0.01, now=t)
    checks.append(
        (
            "F1 silencioso + M Touchpad 500ms",
            (not sm.modo_veiculo) and st_f1.is_neutral() and st_m0.buttons[PSButton.TOUCHPAD]
            and st_m300.buttons[PSButton.TOUCHPAD] and not st_m550.buttons[PSButton.TOUCHPAD],
            f"F1_neutro={st_f1.is_neutral()}, M(0/300/550ms)={st_m0.buttons[PSButton.TOUCHPAD]}/{st_m300.buttons[PSButton.TOUCHPAD]}/{st_m550.buttons[PSButton.TOUCHPAD]}",
        )
    )

    # 8. Múltiplas teclas para mesma ação + Override de Modo > Globais + Rampa LX Veículo
    multi_cfg = GtaConfig(
        feedback_sonoro=False,
        tempo_rampa_direcao_lx_ms=100.0,
        globais={
            KEY_NAME_TO_CODE["A"]: "LX-",
            KEY_NAME_TO_CODE["D"]: "LX+",
            KEY_NAME_TO_CODE["LEFT"]: "DPAD_LEFT",
            KEY_NAME_TO_CODE["RIGHT"]: "DPAD_RIGHT",
        },
        modo_a_pe={
            KEY_NAME_TO_CODE["LALT"]: "MOD_LENTO",
            MOUSE_X1: "R1",
            SCROLL_UP: "TRIANGLE",
        },
        modo_veiculo={
            KEY_NAME_TO_CODE["LEFT"]: "LX-",
            KEY_NAME_TO_CODE["RIGHT"]: "LX+",
            KEY_NAME_TO_CODE["S"]: "L2_RAMPA",
        },
    )
    sm_m = GtaStateMachine(multi_cfg)
    # A pé: LEFT -> DPAD_LEFT (Global) sem LX-; A + LALT -> LX reduzido (~-0.45); Mouse4 -> R1; ScrollUp -> Triângulo
    st_foot_m = sm_m.step(
        InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["LEFT"], KEY_NAME_TO_CODE["A"], KEY_NAME_TO_CODE["LALT"], MOUSE_X1, SCROLL_UP}),
        dt=0.01,
        now=1.0,
    )
    foot_m_ok = (
        st_foot_m.dpad == DPadDirection.LEFT
        and abs(st_foot_m.lx - (-0.45)) < 0.02
        and st_foot_m.buttons[PSButton.R1]
        and st_foot_m.buttons[PSButton.TRIANGLE]
    )
    # Em veículo: LEFT sobrescreve DPAD_LEFT -> aciona SOMENTE LX- com rampa 100ms (DPAD fica NONE!)
    sm_m.toggle_mode(set(), "test")
    lx_ramp_samples: list[float] = []
    for k in range(10):
        st_veh_m = sm_m.step(
            InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["LEFT"], KEY_NAME_TO_CODE["S"]}),
            dt=0.01,
            now=2.0 + k * 0.01,
        )
        lx_ramp_samples.append(round(st_veh_m.lx, 2))
    st_veh_rel = sm_m.step(InputSnapshot(pressed_keys=set()), dt=0.01, now=2.12)
    override_ok = (
        foot_m_ok
        and st_veh_m.dpad == DPadDirection.NONE
        and lx_ramp_samples[0] == -0.10
        and lx_ramp_samples[-1] == -1.00
        and st_veh_m.l2 == 1.0
        and st_veh_rel.lx == 0.0
    )
    checks.append(
        (
            "Múltiplas teclas + Override Modo>Globais + Rampa LX/L2 + Mouse4/Scroll + Mod Lento",
            override_ok,
            f"foot_ok={foot_m_ok}, veh_dpad={st_veh_m.dpad.value}, lx_rampa={lx_ramp_samples}, snap0={st_veh_rel.lx}",
        )
    )

    # 9. Fase 2: Modo Voo (F2) + CROSS_TAP Sprint + L2_TOGGLE Mira + Hotkeys Sensibilidade (F7/F8)
    f2_cfg = GtaConfig(feedback_sonoro=False, sprint_tap_ms=65.0)
    f2_cfg.modo_a_pe[KEY_NAME_TO_CODE["LSHIFT"]] = "CROSS_TAP"
    f2_cfg.modo_a_pe[MOUSE_RIGHT] = "L2_TOGGLE"
    sm_f2 = GtaStateMachine(f2_cfg)
    # CROSS_TAP pulsa ✕ em 0ms (True), 70ms (False), 140ms (True)
    st_t0 = sm_f2.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["LSHIFT"]}), dt=0.01, now=10.00)
    st_t1 = sm_f2.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["LSHIFT"]}), dt=0.01, now=10.07)
    st_t2 = sm_f2.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["LSHIFT"]}), dt=0.01, now=10.14)
    tap_ok = st_t0.buttons[PSButton.CROSS] and (not st_t1.buttons[PSButton.CROSS]) and st_t2.buttons[PSButton.CROSS]
    # L2_TOGGLE alterna mira ON (mesmo após soltar) e OFF no 2º clique
    st_a1 = sm_f2.step(InputSnapshot(pressed_keys={MOUSE_RIGHT}), dt=0.01, now=11.00)
    st_a2 = sm_f2.step(InputSnapshot(pressed_keys=set()), dt=0.01, now=11.05)
    st_a3 = sm_f2.step(InputSnapshot(pressed_keys={MOUSE_RIGHT}), dt=0.01, now=11.10)
    toggle_ok = (st_a1.l2 == 1.0) and (st_a2.l2 == 1.0) and (st_a3.l2 == 0.0)
    # F2 ativa MODO VOO (A sobrescreve LX- global com L1!), F volta p/ A Pé
    sm_f2.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["F2"]}), dt=0.01, now=12.00)
    st_fly = sm_f2.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["A"], KEY_NAME_TO_CODE["G"]}), dt=0.01, now=12.05)
    fly_ok = sm_f2.modo_voo and st_fly.buttons[PSButton.L1] and (st_fly.lx == 0.0) and st_fly.buttons[PSButton.L3]
    # F8 aumenta sensibilidade ao vivo e gera toast
    old_sx = f2_cfg.mouse_sens_x
    sm_f2.step(InputSnapshot(pressed_keys={KEY_NAME_TO_CODE["F8"]}), dt=0.01, now=12.10)
    sens_ok = (f2_cfg.mouse_sens_x > old_sx) and ("Sensibilidade" in sm_f2.get_active_toast(now=12.15))
    checks.append(
        (
            "Fase 2: Modo Voo (F2) + CROSS_TAP + L2_TOGGLE + Hotkeys Sensibilidade (F7/F8)",
            tap_ok and toggle_ok and fly_ok and sens_ok,
            f"tap={tap_ok}, l2_toggle={toggle_ok}, modo_voo={fly_ok}, sens_live={sens_ok}",
        )
    )

    # 10. Win32 Raw Input + Hooks LL start/stop limpo
    if platform.system() == "Windows":
        cap = Win32InputCapture(
            managed_vks=cfg_file.all_managed_keyboard_vks(),
            pause_vk=cfg_file.vk_toggle_pause,
            should_capture=lambda: False,
            should_suppress=lambda: False,
            managed_mouse_codes=cfg_file.all_managed_mouse_codes(),
            scroll_pulso_ms=cfg_file.scroll_pulso_ms,
        )
        try:
            cap.start()
            time.sleep(0.05)
            cap.stop()
            checks.append(("Win32 Raw Input + Hooks LL (start/stop limpo)", True, "OK"))
        except Exception as exc:
            checks.append(("Win32 Raw Input + Hooks LL (start/stop limpo)", False, str(exc)))

    # 10. Integração real VDS4Gamepad
    if probe_vigembus_driver().ready_for_virtual_device:
        ctrl = VirtualPlayStationController(backend=VDS4ViGEmBackend())
        try:
            ctrl.start()
            ctrl.set_full_state(st)
            time.sleep(0.05)
            ctrl.reset_all()
            time.sleep(0.03)
            checks.append(
                (
                    "Integração atômica no VDS4Gamepad real (ViGEmBus)",
                    ctrl.get_raw_report().is_neutral(),
                    "Estado aplicado e zerado com sucesso.",
                )
            )
        finally:
            ctrl.close()

    # 12. Fase 3: Foco Multi-Cliente (Chiaki / PS Remote Play / PXPlay) + Semáforo de Latência
    focus_ok = (
        is_remote_play_window("chiaki.exe", "Chiaki-ng")
        and is_remote_play_window("RemotePlay.exe", "PS Remote Play")
        and is_remote_play_window("pxplay.exe", "PXPlay")
        and (not is_remote_play_window("notepad.exe", "Sem título - Bloco de Notas"))
        and (not is_remote_play_window("explorer.exe", "projeto teclado e mouse gta 6 remote play"))
        and (not is_remote_play_window("chrome.exe", "Chiaki-ng GitHub - PS5 Remote Play"))
    )
    health_items = run_latency_health_check()
    health_ok = bool(health_items) and all(
        item.get("status") in ("green", "yellow", "red") for item in health_items
    )
    checks.append(
        (
            "Fase 3: Foco Multi-Cliente (Chiaki/PS Remote/PXPlay) + Semáforo de Latência",
            focus_ok and health_ok,
            f"focus_clients={focus_ok}, health_checks={len(health_items)}",
        )
    )

    print("=" * 78)
    print("AUTOTESTES GTA TECLADO + MOUSE/TRACKPAD -> DS4 VIRTUAL")
    print("=" * 78)
    for idx, (name, ok, details) in enumerate(checks, start=1):
        print(f"[{'PASS' if ok else 'FAIL'}] {idx:02d}. {name}")
        print(f"       {details}")
    print("-" * 78)
    total_ok = all(ok for _, ok, _ in checks)
    print(f"RESUMO: {sum(ok for _, ok, _ in checks)}/{len(checks)} aprovados ({'SUCESSO' if total_ok else 'FALHA'}).")
    print("=" * 78)
    return total_ok


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Conversor Teclado + Mouse/Trackpad -> DualShock 4 Virtual (GTA PS5)."
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=DEFAULT_MAPPING_FILE,
        help="Caminho para o arquivo JSON de mapeamento (padrão: mapeamento.json).",
    )
    parser.add_argument(
        "--global",
        dest="global_mode",
        action="store_true",
        help="Atua em qualquer janela (desativa foco exclusivo no Chiaki-ng).",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="Inicia pausado (pressione F12 após clicar no Chiaki-ng para ativar).",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Executa em modo simulação em memória (sem driver ViGEmBus).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Executa bateria automatizada de validação.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    args = parse_args(argv)
    if args.self_test:
        return 0 if run_self_tests() else 1

    try:
        cfg = load_config_from_json(args.map)
    except Exception as exc:
        print(f"\n[ERRO] Falha ao ler '{args.map}':\n{exc}")
        if sys.stdin is not None and sys.stdin.isatty():
            try:
                input("\nCorrija o arquivo e pressione Enter para fechar...")
            except EOFError:
                pass
        return 1

    if args.global_mode:
        cfg.apenas_chiaki_em_foco = False
    if args.paused:
        cfg.iniciar_ativo = False

    app = GtaKeyboardMouseApp(config=cfg, mapping_path=args.map, simulate=args.simulate)
    exit_code = app.run()
    if exit_code != 0 and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nOcorreu um erro. Pressione Enter para fechar...")
        except EOFError:
            pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
