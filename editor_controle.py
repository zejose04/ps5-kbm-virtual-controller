"""Editor Visual Interativo do Controle PlayStation (mapeamento.json).

Permite visualizar um controle PlayStation interativo na tela, alternar entre
os modos (A Pé, Veículo e Globais), clicar em qualquer botão/analógico/gatilho
para atribuir uma tecla do teclado ou clique do mouse, ajustar sliders de
sensibilidade/deadzone/rampa do R2 e salvar diretamente em `mapeamento.json`.

Uso:
    Duplo clique em `abrir_editor.bat` ou:
        python editor_controle.py
        python editor_controle.py --self-test
"""

from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import platform
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any
import webbrowser

from config import VIGEMBUS_RECOMMENDED_VERSION, VIGEMBUS_RELEASE_URL
from diagnostico import format_environment_report
from gamepad import (
    BackendDependencyError,
    DriverUnavailableError,
    probe_vigembus_driver,
)
from gta_controle import (
    DEFAULT_MAPPING_FILE,
    GtaKeyboardMouseApp,
    KEY_NAME_TO_CODE,
    check_chiaki_conflicting_keymap,
    check_chiaki_uipi_mismatch,
    get_app_dir,
    load_config_from_json,
    run_latency_health_check,
)


def get_asset_path(rel_path: str) -> Path:
    """Resolve caminhos de recursos (`assets/...`) tanto em .py quanto em .exe PyInstaller."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / rel_path
        if cand.exists():
            return cand
    return Path(__file__).resolve().parent / rel_path


# ===========================================================================
# Internacionalização PT-BR / EN (troca instantânea sem reiniciar)
# ===========================================================================

I18N: dict[str, dict[str, str]] = {
    "pt": {
        "win_title": "GTA PS5 Controle Virtual v1.0.1 — Editor, Perfis, Overlay & Semáforo",
        "header_title": "Controle Virtual PlayStation (Chiaki-ng / PS Remote Play / PXPlay)",
        "profile_lbl": "Perfil:",
        "save_as": "Salvar Como",
        "import": "Importar",
        "export": "Exportar",
        "tab_foot": "A PÉ",
        "tab_vehicle": "VEÍCULO",
        "tab_flight": "VOO",
        "tab_globals": "GLOBAIS",
        "sidebar_title": "Mouse / Trackpad e Rampas",
        "btn_diag": "Semáforo & Diag.",
        "btn_reset": "Restaurar Padrão",
        "btn_save": "Salvar",
        "btn_start": "▶ Salvar e Iniciar Controle",
        "btn_stop": "⏹ Parar Controle Virtual",
        "banner_stopped": "MOTOR PARADO — Edite seu mapeamento acima e clique em '▶ Salvar e Iniciar Controle'.",
        "status_ready": "Clique em qualquer botão do controle para remapear ou clique em Iniciar Controle.",
        "sl_sens_x": "Sensibilidade X (Câmera)",
        "sl_sens_y": "Sensibilidade Y (Câmera)",
        "sl_ads": "Multiplicador Mira (ADS)",
        "sl_dz": "Compensação Deadzone GTA",
        "sl_smooth": "Suavização (0.00 = menor lag)",
        "sl_timeout": "Retorno ao Centro (ms)",
        "sl_r2": "Rampa Acelerador R2 (ms)",
        "sl_lx": "Rampa Direção Veículo A/D (ms)",
        "cb_inv_y": "Inverter eixo Y do Mouse/Trackpad",
        "cb_trackpad": "Ativar captura de Trackpad (Notebook)",
        "cb_focus": "Somente com Remote Play em foco (Chiaki / PS Remote / PXPlay)",
        "cb_block": "Bloquear teclas no Windows (evita conflito)",
        "cb_beep": "Feedback sonoro ao trocar A Pé / Veículo / Voo",
        "cb_overlay": "Mostrar Mini-Overlay flutuante sobre o jogo (F7/F8 Sens)",
        "cap_foot": "Editando: MODO A PÉ  (⚡ = Sprint Auto-Tap | ⇄ = Mira Toggle | [G] = Globais)",
        "cap_vehicle": "Editando: MODO VEÍCULO  (sobrescreve Globais | ~ = rampa | [G] = Globais)",
        "cap_flight": "Editando: MODO VOO / AERONAVE  (F2 alterna | sobrescreve Globais | ~ = rampa)",
        "cap_globals": "Editando: TECLAS GLOBAIS (ativas em todos os modos, salvo sobrescritas)",
        "diag_title": "Semáforo de Latência & Diagnóstico em 4 Camadas",
        "diag_sec_health": "Semáforo de Latência (Somente Leitura — Chiaki-ng / ViGEmBus)",
        "diag_refresh": "Atualizar Semáforo",
        "diag_download": "Baixar ViGEmBus 1.22.0 (GitHub Oficial)",
        "diag_close": "Fechar",
        "lang_btn": "🌐 PT / EN",
    },
    "en": {
        "win_title": "PS5 Virtual Controller v1.0.1 — Visual Editor, Profiles, Overlay & Health",
        "header_title": "PlayStation Virtual Controller (Chiaki-ng / PS Remote Play / PXPlay)",
        "profile_lbl": "Profile:",
        "save_as": "Save As",
        "import": "Import",
        "export": "Export",
        "tab_foot": "ON FOOT",
        "tab_vehicle": "VEHICLE",
        "tab_flight": "FLIGHT",
        "tab_globals": "GLOBALS",
        "sidebar_title": "Mouse / Trackpad & Ramps",
        "btn_diag": "Health & Diag.",
        "btn_reset": "Reset Default",
        "btn_save": "Save",
        "btn_start": "▶ Save & Start Controller",
        "btn_stop": "⏹ Stop Virtual Controller",
        "banner_stopped": "ENGINE STOPPED — Customize your mapping above and click '▶ Save & Start Controller'.",
        "status_ready": "Click any controller button to remap, or click Start Controller.",
        "sl_sens_x": "X Sensitivity (Camera)",
        "sl_sens_y": "Y Sensitivity (Camera)",
        "sl_ads": "ADS Aim Multiplier",
        "sl_dz": "Game Deadzone Compensation",
        "sl_smooth": "Smoothing (0.00 = lowest lag)",
        "sl_timeout": "Center Return Timeout (ms)",
        "sl_r2": "R2 Throttle Ramp (ms)",
        "sl_lx": "Vehicle Steering A/D Ramp (ms)",
        "cb_inv_y": "Invert Mouse/Trackpad Y-axis",
        "cb_trackpad": "Enable Laptop Trackpad Capture",
        "cb_focus": "Active only when Remote Play focused (Chiaki / PS Remote / PXPlay)",
        "cb_block": "Suppress mapped keys in Windows (prevents conflicts)",
        "cb_beep": "Audio beep on Foot / Vehicle / Flight switch",
        "cb_overlay": "Show Click-Through Mini-Overlay HUD (F7/F8 Sens)",
        "cap_foot": "Editing: ON FOOT  (⚡ = Auto-Tap Sprint | ⇄ = Toggle Aim | [G] = Globals)",
        "cap_vehicle": "Editing: VEHICLE  (overrides Globals | ~ = ramp | [G] = Globals)",
        "cap_flight": "Editing: FLIGHT / AIRCRAFT  (F2 toggles | overrides Globals | ~ = ramp)",
        "cap_globals": "Editing: GLOBAL KEYS (active in all modes unless overridden)",
        "diag_title": "Latency Traffic-Light & 4-Layer Diagnostics",
        "diag_sec_health": "Latency Health Check (Read-Only — Chiaki-ng / ViGEmBus)",
        "diag_refresh": "Refresh Check",
        "diag_download": "Download ViGEmBus 1.22.0 (Official GitHub)",
        "diag_close": "Close",
        "lang_btn": "🌐 EN / PT",
    },
}


# ===========================================================================
# Paleta visual (tema escuro estilo PlayStation)
# ===========================================================================

BG_ROOT = "#0d1117"
BG_PANEL = "#161b22"
BG_CARD = "#1f2633"
BG_CTRL_BODY = "#202736"
BG_CTRL_INNER = "#141923"
BG_CTRL_BTN = "#2b3447"
BG_CTRL_BTN_HOVER = "#394867"
BORDER_SUBTLE = "#303a4d"
FG_TEXT = "#e6edf3"
FG_MUTED = "#8b949e"

ACCENT_FOOT = "#0088ff"     # Azul PlayStation (Modo A Pé)
ACCENT_VEHICLE = "#ff922b"  # Âmbar (Modo Veículo)
ACCENT_FLIGHT = "#22b8cf"   # Ciano (Modo Voo / Aeronave)
ACCENT_GLOBAL = "#9775fa"   # Roxo (Globais)
ACCENT_LIVE_GLOW = "#2fb344"  # Destaque ao vivo quando pressionado
ACCENT_OK = "#2fb344"
ACCENT_WARN = "#f59f00"
ACCENT_DANGER = "#e03131"

PS_COLOR_TRIANGLE = "#38d9a9"
PS_COLOR_CIRCLE = "#ff6b6b"
PS_COLOR_CROSS = "#4dabf7"
PS_COLOR_SQUARE = "#f06595"


# ===========================================================================
# Perfil padrão de restauração (espelha mapeamento.json)
# ===========================================================================

DEFAULT_JSON_DATA: dict[str, Any] = {
    "_ajuda": "Edite visualmente no Editor do Controle ou manualmente no Bloco de Notas.",
    "_teclas_aceitas": "A-Z, 0-9, SPACE, LSHIFT, RSHIFT, LCTRL, RCTRL, LALT, TAB, ENTER, BACKSPACE, ESC, CAPSLOCK, UP, DOWN, LEFT, RIGHT, F1-F12, CEDILHA, COMMA, PERIOD, MINUS, SLASH, ABNT_SLASH, OEM_102, NUM0-NUM9, NUM_ADD, NUM_SUB, MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE, MOUSE_X1, MOUSE_X2, SCROLL_UP, SCROLL_DOWN",
    "_acoes_aceitas": "CROSS, CROSS_TAP, CIRCLE, SQUARE, TRIANGLE, L1, R1, L2, L2_RAMPA, L2_TOGGLE, R2, R2_RAMPA, L3, R3, L3+R3, OPTIONS, SHARE, PS, TOUCHPAD_TAP, TOUCHPAD_HOLD_500, DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT, LX-, LX+, LY+, LY-, MOD_LENTO",
    "mouse_e_trackpad": {
        "sensibilidade_x": 0.250,
        "sensibilidade_y": 0.220,
        "mult_mira_ads": 1.25,
        "deadzone_entrada": 0.0,
        "deadzone_compensacao": 0.18,
        "curva_expo": 0.65,
        "suavizacao": 0.0,
        "decaimento_parada": 0.88,
        "timeout_centro_ms": 45.0,
        "inverter_y": False,
        "usar_captura_cursor_trackpad": True,
    },
    "ajustes": {
        "tempo_rampa_r2_ms": 100.0,
        "tempo_rampa_l2_ms": 100.0,
        "tempo_rampa_direcao_lx_ms": 90.0,
        "fator_caminhada_lenta": 0.45,
        "scroll_pulso_ms": 75.0,
        "sprint_tap_ms": 65.0,
        "touchpad_segurar_m_ms": 500.0,
        "touchpad_toque_v_ms": 80.0,
        "triangulo_pulso_f_ms": 90.0,
        "taxa_atualizacao_hz": 200.0,
        "inverter_y_esquerdo": False,
        "iniciar_ativo": True,
        "apenas_quando_chiaki_em_foco": True,
        "bloquear_entradas_no_windows": True,
        "exigir_soltar_tecla_ao_trocar_modo": True,
        "feedback_sonoro": True,
        "mostrar_mini_overlay": True,
        "idioma": "pt",
    },
    "controles_sistema": {
        "alternar_modo_e_triangulo": "F",
        "alternar_modo_silencioso": "F1",
        "alternar_modo_voo": "F2",
        "sensibilidade_menos": "F7",
        "sensibilidade_mais": "F8",
        "pausar_ativar": "F12",
    },
    "globais": {
        "A": "LX-",
        "D": "LX+",
        "TAB": "L1",
        "V": "TOUCHPAD_TAP",
        "M": "TOUCHPAD_HOLD_500",
        "C": "R3",
        "ESC": "OPTIONS",
        "P": "OPTIONS",
        "H": "DPAD_RIGHT",
        "ENTER": "CROSS",
        "BACKSPACE": "CIRCLE",
        "CAPSLOCK": "L3+R3",
        "UP": "DPAD_UP",
        "DOWN": "DPAD_DOWN",
        "LEFT": "DPAD_LEFT",
        "RIGHT": "DPAD_RIGHT",
    },
    "modo_a_pe": {
        "W": "LY+",
        "S": "LY-",
        "LSHIFT": "CROSS",
        "SPACE": "SQUARE",
        "LCTRL": "L3",
        "Q": "R1",
        "R": "CIRCLE",
        "E": "DPAD_RIGHT",
        "MOUSE_RIGHT": "L2",
        "MOUSE_LEFT": "R2",
    },
    "modo_veiculo": {
        "W": "R2_RAMPA",
        "S": "L2",
        "SPACE": "R1",
        "LSHIFT": "LY+",
        "LCTRL": "LY-",
        "MOUSE_LEFT": "L1",
        "MOUSE_RIGHT": "R1",
        "Q": "CIRCLE",
        "E": "L3",
    },
    "modo_voo": {
        "W": "R2_RAMPA",
        "S": "L2_RAMPA",
        "A": "L1",
        "D": "R1",
        "UP": "LY+",
        "DOWN": "LY-",
        "LEFT": "LX-",
        "RIGHT": "LX+",
        "NUM8": "LY+",
        "NUM5": "LY-",
        "NUM4": "LX-",
        "NUM6": "LX+",
        "SPACE": "CROSS",
        "MOUSE_LEFT": "CROSS",
        "MOUSE_RIGHT": "CIRCLE",
        "G": "L3",
        "TAB": "SQUARE",
    },
}


def _make_rdr2_profile() -> dict[str, Any]:
    prof = copy.deepcopy(DEFAULT_JSON_DATA)
    prof["globais"].update({"H": "DPAD_UP", "CAPSLOCK": "L3+R3"})
    prof["modo_a_pe"] = {
        "W": "LY+",
        "S": "LY-",
        "LSHIFT": "CROSS_TAP",
        "SPACE": "SQUARE",
        "LCTRL": "L3",
        "Q": "R1",
        "R": "CIRCLE",
        "E": "SQUARE",
        "LALT": "MOD_LENTO",
        "MOUSE_RIGHT": "L2",
        "MOUSE_LEFT": "R2",
        "MOUSE_MIDDLE": "R3",
    }
    prof["modo_veiculo"] = {
        "W": "LY+",
        "S": "LY-",
        "LSHIFT": "CROSS_TAP",
        "LCTRL": "R1",
        "SPACE": "SQUARE",
        "MOUSE_RIGHT": "L2",
        "MOUSE_LEFT": "R2",
        "Q": "CIRCLE",
        "E": "L3",
    }
    return prof


def _make_fps_profile() -> dict[str, Any]:
    prof = copy.deepcopy(DEFAULT_JSON_DATA)
    prof["modo_a_pe"] = {
        "W": "LY+",
        "S": "LY-",
        "SPACE": "CROSS",
        "LSHIFT": "L3",
        "LCTRL": "CIRCLE",
        "C": "CIRCLE",
        "R": "SQUARE",
        "E": "SQUARE",
        "Q": "TRIANGLE",
        "G": "R1",
        "MOUSE_LEFT": "R2",
        "MOUSE_RIGHT": "L2",
        "MOUSE_X1": "R1",
        "MOUSE_X2": "L1",
        "MOUSE_MIDDLE": "R3",
    }
    return prof


BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "GTA V / VI (Padrão)": DEFAULT_JSON_DATA,
    "Red Dead Redemption 2": _make_rdr2_profile(),
    "Shooter / FPS (PS5)": _make_fps_profile(),
}

CAMERA_PRESETS: dict[str, dict[str, Any]] = {
    "mouse_gamer": {
        "label": "Mouse Gamer",
        "desc": "Mouse 500–1000 Hz (Raw Input puro, resposta imediata)",
        "mouse_e_trackpad": {
            "sensibilidade_x": 0.14,
            "sensibilidade_y": 0.12,
            "mult_mira_ads": 1.15,
            "deadzone_compensacao": 0.16,
            "suavizacao": 0.0,
            "decaimento_parada": 0.80,
            "timeout_centro_ms": 30.0,
            "usar_captura_cursor_trackpad": False,
        },
    },
    "mouse_comum": {
        "label": "Mouse 125Hz",
        "desc": "Mouse USB / sem fio padrão (equilibrado)",
        "mouse_e_trackpad": {
            "sensibilidade_x": 0.20,
            "sensibilidade_y": 0.17,
            "mult_mira_ads": 1.25,
            "deadzone_compensacao": 0.18,
            "suavizacao": 0.0,
            "decaimento_parada": 0.85,
            "timeout_centro_ms": 40.0,
            "usar_captura_cursor_trackpad": False,
        },
    },
    "trackpad": {
        "label": "Trackpad",
        "desc": "Trackpad de notebook (captura híbrida + alta sensibilidade)",
        "mouse_e_trackpad": {
            "sensibilidade_x": 0.25,
            "sensibilidade_y": 0.22,
            "mult_mira_ads": 1.25,
            "deadzone_compensacao": 0.18,
            "suavizacao": 0.0,
            "decaimento_parada": 0.88,
            "timeout_centro_ms": 45.0,
            "usar_captura_cursor_trackpad": True,
        },
    },
}


# ===========================================================================
# Conversão de eventos Tk KeyPress -> nomes oficiais de mapeamento.json
# ===========================================================================

TK_KEYSYM_TO_NAME: dict[str, str] = {
    "space": "SPACE",
    "Shift_L": "LSHIFT",
    "Shift_R": "RSHIFT",
    "Control_L": "LCTRL",
    "Control_R": "RCTRL",
    "Alt_L": "LALT",
    "Tab": "TAB",
    "ISO_Left_Tab": "TAB",
    "Return": "ENTER",
    "KP_Enter": "ENTER",
    "BackSpace": "BACKSPACE",
    "Escape": "ESC",
    "Caps_Lock": "CAPSLOCK",
    "Up": "UP",
    "Down": "DOWN",
    "Left": "LEFT",
    "Right": "RIGHT",
    "Prior": "PAGEUP",
    "Next": "PAGEDOWN",
    "Home": "HOME",
    "End": "END",
    "Insert": "INSERT",
    "Delete": "DELETE",
    "ccedilla": "CEDILHA",
    "Ccedilla": "CEDILHA",
    "comma": "COMMA",
    "less": "COMMA",
    "period": "PERIOD",
    "greater": "PERIOD",
    "minus": "MINUS",
    "underscore": "MINUS",
    "slash": "SLASH",
    "question": "SLASH",
    "backslash": "OEM_102",
    "bar": "OEM_102",
    "KP_Add": "NUM_ADD",
    "KP_Subtract": "NUM_SUB",
}
for _i in range(1, 13):
    TK_KEYSYM_TO_NAME[f"F{_i}"] = f"F{_i}"
for _n in range(10):
    TK_KEYSYM_TO_NAME[f"KP_{_n}"] = f"NUM{_n}"

_VK_FALLBACK_TO_NAME: dict[int, str] = {
    0x6B: "NUM_ADD",
    0x6D: "NUM_SUB",
    0xA4: "LALT",
    0xBA: "CEDILHA",
    0xBC: "COMMA",
    0xBD: "MINUS",
    0xBE: "PERIOD",
    0xBF: "SLASH",
    0xC1: "ABNT_SLASH",
    0xE2: "OEM_102",
}


def tk_event_to_key_name(keysym: str, keycode: int = 0) -> str | None:
    """Converte um keysym/keycode do Tkinter para o nome aceito em `mapeamento.json`."""
    if keysym in TK_KEYSYM_TO_NAME:
        return TK_KEYSYM_TO_NAME[keysym]
    if len(keysym) == 1 and keysym.upper() in KEY_NAME_TO_CODE:
        return keysym.upper()
    if 0x60 <= keycode <= 0x69:
        return f"NUM{keycode - 0x60}"
    if keycode in _VK_FALLBACK_TO_NAME:
        return _VK_FALLBACK_TO_NAME[keycode]
    # Fallback por Virtual-Key Windows para letras/números sob Shift/Caps
    if 0x41 <= keycode <= 0x5A or 0x30 <= keycode <= 0x39:
        ch = chr(keycode)
        if ch in KEY_NAME_TO_CODE:
            return ch
    return None


def pretty_key(key_name: str) -> str:
    """Formata o nome da tecla de forma curta e clara para caber no controle visual."""
    short = {
        "MOUSE_LEFT": "M.Esq",
        "MOUSE_RIGHT": "M.Dir",
        "MOUSE_MIDDLE": "M.Meio",
        "MOUSE_X1": "M4",
        "MOUSE_X2": "M5",
        "SCROLL_UP": "Scr↑",
        "SCROLL_DOWN": "Scr↓",
        "LSHIFT": "LShift",
        "RSHIFT": "RShift",
        "LCTRL": "LCtrl",
        "RCTRL": "RCtrl",
        "LALT": "LAlt",
        "SPACE": "Espaço",
        "BACKSPACE": "Bksp",
        "CAPSLOCK": "Caps",
        "ENTER": "Enter",
        "ESCAPE": "Esc",
        "ESC": "Esc",
        "UP": "↑",
        "DOWN": "↓",
        "LEFT": "←",
        "RIGHT": "→",
        "CEDILHA": "Ç",
        "Ç": "Ç",
        "COMMA": ",",
        "MINUS": "-",
        "PERIOD": ".",
        "SLASH": "/",
        "ABNT_SLASH": "/?",
        "OEM_102": "\\|",
        "NUM_ADD": "Num+",
        "NUM_SUB": "Num-",
    }
    upper = key_name.upper()
    if upper in short:
        return short[upper]
    if upper.startswith("NUM") and len(upper) == 4 and upper[3].isdigit():
        return f"N{upper[3]}"
    return upper


ACTION_DESCRIPTIONS: dict[str, str] = {
    "CROSS": "Cruz (✕)",
    "CROSS_TAP": "Cruz Auto-Tap (Sprint GTA ⚡)",
    "CIRCLE": "Círculo (○)",
    "SQUARE": "Quadrado (□)",
    "TRIANGLE": "Triângulo (△)",
    "L1": "L1 (Ombro Esq.)",
    "R1": "R1 (Ombro Dir.)",
    "L2": "L2 (Gatilho Esq.)",
    "L2_RAMPA": "L2 c/ Rampa (~100ms)",
    "L2_TOGGLE": "L2 Alternar Mira (Toggle ⇄)",
    "R2": "R2 (Gatilho Dir.)",
    "R2_RAMPA": "R2 c/ Rampa (~100ms)",
    "L3": "L3 (Clique Analóg. Esq.)",
    "R3": "R3 (Clique Analóg. Dir.)",
    "L3+R3": "Habilidade Especial (L3+R3)",
    "OPTIONS": "Options (Pause/Mapa)",
    "SHARE": "Share / Create",
    "PS": "Botão PS",
    "TOUCHPAD_TAP": "Touchpad (Toque / Câmera)",
    "TOUCHPAD_HOLD_500": "Touchpad (Segurar 500ms / Menu)",
    "DPAD_UP": "D-Pad Cima",
    "DPAD_DOWN": "D-Pad Baixo",
    "DPAD_LEFT": "D-Pad Esquerda",
    "DPAD_RIGHT": "D-Pad Direita",
    "LX-": "Analóg. Esq. Esquerda (LX-)",
    "LX+": "Analóg. Esq. Direita (LX+)",
    "LY+": "Analóg. Esq. Cima / Frente (LY+)",
    "LY-": "Analóg. Esq. Baixo / Trás (LY-)",
    "MOD_LENTO": "Caminhada Lenta (45% velocidade)",
}


# ===========================================================================
# Janela modal de captura de tecla/clique do mouse
# ===========================================================================

class KeyCaptureDialog(tk.Toplevel):
    """Modal de captura multi-tecla: exibe pílulas [Tecla ×], mouse/scroll e rampa."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        action_label: str,
        current_keys: list[str],
        inherited_keys: list[str] | None = None,
        allow_clear: bool = True,
        allow_multi: bool = True,
        allow_mouse: bool = True,
        allow_variant_toggle: bool = False,
        variant_label: str = "Usar modo alternativo",
        initial_variant: bool = False,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG_PANEL)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result_key: str | None = None
        # replace | add | remove_one | clear | update_variant | cancel
        self.result_mode: str = "cancel"
        self.use_variant = tk.BooleanVar(value=initial_variant)
        # Se já existem 2+ teclas nesta ação, padrão é "+ Adicionar" para evitar apagar tudo sem querer
        default_multi = "add" if (allow_multi and len(current_keys) >= 2) else "replace"
        self.capture_mode = tk.StringVar(value=default_multi)

        pad = tk.Frame(self, bg=BG_PANEL, padx=18, pady=14)
        pad.pack(fill="both", expand=True)

        tk.Label(
            pad,
            text=action_label,
            font=("Segoe UI", 11, "bold"),
            fg=FG_TEXT,
            bg=BG_PANEL,
        ).pack(anchor="w")

        # Pílulas interativas [ Tecla  × ] das teclas definidas neste modo
        pills_header = tk.Frame(pad, bg=BG_PANEL)
        pills_header.pack(fill="x", pady=(6, 4))
        tk.Label(
            pills_header,
            text="Teclas neste modo:" if current_keys else "Nenhuma tecla exclusiva neste modo.",
            font=("Segoe UI", 8, "bold"),
            fg=FG_MUTED,
            bg=BG_PANEL,
        ).pack(side="left")
        if current_keys and allow_clear:
            tk.Label(
                pills_header,
                text="(clique no × para remover só uma)",
                font=("Segoe UI", 8),
                fg=FG_MUTED,
                bg=BG_PANEL,
            ).pack(side="right")

        if current_keys:
            pills_row = tk.Frame(pad, bg=BG_PANEL)
            pills_row.pack(fill="x", pady=(0, 6))
            for k in current_keys:
                pill = tk.Frame(
                    pills_row,
                    bg="#243147",
                    highlightthickness=1,
                    highlightbackground=ACCENT_FOOT,
                )
                pill.pack(side="left", padx=(0, 6), pady=2)
                tk.Label(
                    pill,
                    text=pretty_key(k),
                    font=("Segoe UI", 9, "bold"),
                    fg=FG_TEXT,
                    bg="#243147",
                    padx=7,
                    pady=2,
                ).pack(side="left")
                if allow_clear:
                    tk.Button(
                        pill,
                        text="×",
                        font=("Segoe UI", 9, "bold"),
                        bg="#3b1d24",
                        fg="#ff8787",
                        activebackground="#5c2430",
                        activeforeground="#ffc9c9",
                        relief="flat",
                        padx=5,
                        pady=0,
                        cursor="hand2",
                        command=lambda key_to_rm=k: self._remove_one(key_to_rm),
                    ).pack(side="left")

        if inherited_keys:
            inh_txt = ", ".join(pretty_key(k) for k in inherited_keys)
            tk.Label(
                pad,
                text=f"Também ativas via GLOBAIS: {inh_txt}  (para remover, edite na aba GLOBAIS)",
                font=("Segoe UI", 8),
                fg=ACCENT_GLOBAL,
                bg=BG_PANEL,
            ).pack(anchor="w", pady=(0, 6))

        # Escolha explícita entre Substituir e + Adicionar tecla extra
        if allow_multi and current_keys:
            mode_row = tk.Frame(pad, bg=BG_CARD, padx=10, pady=6)
            mode_row.pack(fill="x", pady=(0, 8))
            tk.Radiobutton(
                mode_row,
                text="Substituir tecla(s) atual(is)",
                variable=self.capture_mode,
                value="replace",
                bg=BG_CARD,
                fg=FG_TEXT,
                selectcolor=BG_ROOT,
                activebackground=BG_CARD,
                activeforeground=FG_TEXT,
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left", padx=(0, 12))
            tk.Radiobutton(
                mode_row,
                text="+ Adicionar tecla extra (multi-tecla)",
                variable=self.capture_mode,
                value="add",
                bg=BG_CARD,
                fg="#74c0fc",
                selectcolor=BG_ROOT,
                activebackground=BG_CARD,
                activeforeground="#74c0fc",
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left")

        prompt_box = tk.Frame(
            pad,
            bg=BG_CARD,
            highlightthickness=1,
            highlightbackground=ACCENT_FOOT,
            padx=14,
            pady=11,
        )
        prompt_box.pack(fill="x", pady=(0, 10))

        tk.Label(
            prompt_box,
            text="Pressione qualquer tecla no teclado agora...",
            font=("Segoe UI", 10, "bold"),
            fg="#74c0fc",
            bg=BG_CARD,
        ).pack()
        tk.Label(
            prompt_box,
            text="(letras, setas, Numpad, Alt Esq., Ç — ou escolha Mouse/Scroll abaixo)"
            if allow_mouse
            else "(use uma tecla do teclado — ex.: F, F1, F12, Insert, Home)",
            font=("Segoe UI", 8),
            fg=FG_MUTED,
            bg=BG_CARD,
        ).pack(pady=(2, 0))

        # Botões de clique do mouse e Scroll (2 linhas compactas)
        if allow_mouse:
            mouse_row1 = tk.Frame(pad, bg=BG_PANEL)
            mouse_row1.pack(fill="x", pady=(0, 5))
            for idx, (lbl, code_name) in enumerate(
                [
                    ("Mouse Esq.", "MOUSE_LEFT"),
                    ("Mouse Dir.", "MOUSE_RIGHT"),
                    ("Mouse Meio", "MOUSE_MIDDLE"),
                ]
            ):
                tk.Button(
                    mouse_row1,
                    text=lbl,
                    font=("Segoe UI", 8, "bold"),
                    bg=BG_CTRL_BTN,
                    fg=FG_TEXT,
                    activebackground=BG_CTRL_BTN_HOVER,
                    activeforeground=FG_TEXT,
                    relief="flat",
                    padx=8,
                    pady=5,
                    cursor="hand2",
                    command=lambda c=code_name: self._accept(c),
                ).pack(side="left", expand=True, fill="x", padx=(0 if idx == 0 else 4, 0))

            mouse_row2 = tk.Frame(pad, bg=BG_PANEL)
            mouse_row2.pack(fill="x", pady=(0, 8))
            for idx, (lbl, code_name) in enumerate(
                [
                    ("Mouse 4 (Voltar)", "MOUSE_X1"),
                    ("Mouse 5 (Avançar)", "MOUSE_X2"),
                    ("Scroll ↑", "SCROLL_UP"),
                    ("Scroll ↓", "SCROLL_DOWN"),
                ]
            ):
                tk.Button(
                    mouse_row2,
                    text=lbl,
                    font=("Segoe UI", 8, "bold"),
                    bg=BG_CTRL_BTN,
                    fg="#c5d4ea",
                    activebackground=BG_CTRL_BTN_HOVER,
                    activeforeground=FG_TEXT,
                    relief="flat",
                    padx=6,
                    pady=5,
                    cursor="hand2",
                    command=lambda c=code_name: self._accept(c),
                ).pack(side="left", expand=True, fill="x", padx=(0 if idx == 0 else 4, 0))

        if allow_variant_toggle:
            tk.Checkbutton(
                pad,
                text=variant_label,
                variable=self.use_variant,
                bg=BG_PANEL,
                fg=ACCENT_VEHICLE,
                selectcolor=BG_CARD,
                activebackground=BG_PANEL,
                activeforeground=ACCENT_VEHICLE,
                font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", pady=(0, 8))

        bottom = tk.Frame(pad, bg=BG_PANEL)
        bottom.pack(fill="x", pady=(2, 0))
        if allow_clear and current_keys:
            tk.Button(
                bottom,
                text="Limpar todas",
                font=("Segoe UI", 8),
                bg="#3b1d24",
                fg="#ff8787",
                activebackground="#5c2430",
                activeforeground="#ffc9c9",
                relief="flat",
                padx=10,
                pady=5,
                cursor="hand2",
                command=self._clear,
            ).pack(side="left")

        tk.Button(
            bottom,
            text="Cancelar",
            font=("Segoe UI", 8),
            bg=BG_CARD,
            fg=FG_MUTED,
            activebackground=BG_CTRL_BTN,
            activeforeground=FG_TEXT,
            relief="flat",
            padx=12,
            pady=5,
            cursor="hand2",
            command=self.destroy,
        ).pack(side="right")

        if allow_variant_toggle and current_keys:
            tk.Button(
                bottom,
                text="Aplicar opção",
                font=("Segoe UI", 8, "bold"),
                bg=ACCENT_VEHICLE,
                fg="#ffffff",
                activebackground="#e8590c",
                activeforeground="#ffffff",
                relief="flat",
                padx=10,
                pady=5,
                cursor="hand2",
                command=self._apply_variant_only,
            ).pack(side="right", padx=(0, 6))

        self.bind("<KeyPress>", self._on_key)
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(20, px)}+{max(20, py)}")
        self.focus_force()

    def _on_key(self, event: tk.Event[Any]) -> str:
        name = tk_event_to_key_name(event.keysym, getattr(event, "keycode", 0))
        if name is not None:
            self._accept(name)
        return "break"

    def _accept(self, key_name: str) -> None:
        self.result_key = key_name
        self.result_mode = "add" if self.capture_mode.get() == "add" else "replace"
        self.destroy()

    def _remove_one(self, key_name: str) -> None:
        self.result_key = key_name
        self.result_mode = "remove_one"
        self.destroy()

    def _apply_variant_only(self) -> None:
        self.result_key = None
        self.result_mode = "update_variant"
        self.destroy()

    def _clear(self) -> None:
        self.result_key = None
        self.result_mode = "clear"
        self.destroy()


# ===========================================================================
# Mini-Overlay Flutuante Click-Through sobre o Chiaki-ng
# ===========================================================================

class MiniOverlayHUD(tk.Toplevel):
    """Pílula discreta, sempre no topo e click-through (não rouba foco nem cliques)."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#111827")

        self._outer = tk.Frame(
            self,
            bg="#111827",
            highlightthickness=1,
            highlightbackground=ACCENT_FOOT,
        )
        self._outer.pack(fill="both", expand=True)

        self._strip = tk.Frame(self._outer, bg=ACCENT_FOOT, width=6)
        self._strip.pack(side="left", fill="y")

        body = tk.Frame(self._outer, bg="#111827", padx=12, pady=6)
        body.pack(side="left", fill="both", expand=True)

        self._main_lbl = tk.Label(
            body,
            text="● MODO A PÉ  |  Sens 0.25/0.22",
            font=("Segoe UI", 10, "bold"),
            fg="#ffffff",
            bg="#111827",
        )
        self._main_lbl.pack(anchor="w")

        self._sub_lbl = tk.Label(
            body,
            text="",
            font=("Segoe UI", 8, "bold"),
            fg="#ffd43b",
            bg="#111827",
        )
        self._sub_visible = False
        self._last_key: tuple[str, str, str] = ("", "", "")
        self._top_hwnd: int = 0
        self._click_through_applied: bool = False

        self._reposition()
        # Agenda o estilo click-through Win32 DEPOIS do primeiro ciclo de pintura do Tk
        # (evita a tarja preta causada por WS_EX_LAYERED aplicado antes do WM_PAINT!)
        self.after(60, self._apply_win32_click_through)

    def _reposition(self) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        w = max(250, self.winfo_reqwidth())
        h = max(32, self.winfo_reqheight())
        x = max(12, (sw - w) // 2)
        self.geometry(f"{w}x{h}+{x}+12")

    def _resolve_top_hwnd(self) -> int:
        if platform.system() != "Windows":
            return 0
        try:
            user32 = ctypes.windll.user32
            GA_ROOT = 2
            raw_id = int(self.winfo_id())
            user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetAncestor.restype = ctypes.c_void_p
            top = user32.GetAncestor(ctypes.c_void_p(raw_id), GA_ROOT)
            return int(top) if top else raw_id
        except Exception:
            return 0

    def _apply_win32_click_through(self) -> None:
        if platform.system() != "Windows":
            return
        try:
            if not self.winfo_exists():
                return
            self.update()
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            LWA_ALPHA = 0x00000002
            HWND_TOPMOST = ctypes.c_void_p(-1)
            SWP_FLAGS = 0x0001 | 0x0002 | 0x0010 | 0x0020 | 0x0040  # NOSIZE|NOMOVE|NOACTIVATE|FRAMECHANGED|SHOWWINDOW
            RDW_FLAGS = 0x0001 | 0x0004 | 0x0080 | 0x0100 | 0x0400  # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW|FRAME

            hwnd = self._resolve_top_hwnd()
            if not hwnd:
                return
            self._top_hwnd = hwnd

            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_wl = user32.GetWindowLongPtrW
                set_wl = user32.SetWindowLongPtrW
                get_wl.restype = ctypes.c_ssize_t
                set_wl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            else:
                get_wl = user32.GetWindowLongW
                set_wl = user32.SetWindowLongW
            get_wl.argtypes = [ctypes.c_void_p, ctypes.c_int]

            ex_style = int(get_wl(ctypes.c_void_p(hwnd), GWL_EXSTYLE))
            new_style = ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            set_wl(ctypes.c_void_p(hwnd), GWL_EXSTYLE, new_style)

            # OBRIGATÓRIO após adicionar WS_EX_LAYERED: informa alpha GDI (230/255 ≈ 90%)
            # Sem SetLayeredWindowAttributes o DWM compõe a superfície como tarja preta!
            user32.SetLayeredWindowAttributes.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_uint32
            ]
            user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd), 0, 230, LWA_ALPHA)
            user32.SetWindowPos(ctypes.c_void_p(hwnd), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
            user32.RedrawWindow(ctypes.c_void_p(hwnd), None, None, RDW_FLAGS)
            self._click_through_applied = True
        except Exception:
            pass

    def ensure_topmost(self) -> None:
        """Mantém o overlay acima sem roubar foco (chamado periodicamente pelo polling)."""
        if platform.system() != "Windows" or not self._top_hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            HWND_TOPMOST = ctypes.c_void_p(-1)
            SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE|NOMOVE|NOACTIVATE
            user32.SetWindowPos(ctypes.c_void_p(self._top_hwnd), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        except Exception:
            pass

    def update_from_status(self, st: dict[str, Any], lang: str = "pt") -> None:
        is_en = lang == "en"
        if not st.get("ativo", True):
            accent = ACCENT_GLOBAL
            main_txt = "⏸ PAUSED (F12 to resume)" if is_en else "⏸ PAUSADO (F12 p/ ativar)"
        else:
            mode_id = st.get("modo_atual", "modo_a_pe")
            accent = {
                "modo_a_pe": ACCENT_FOOT,
                "modo_veiculo": ACCENT_VEHICLE,
                "modo_voo": ACCENT_FLIGHT,
            }.get(mode_id, ACCENT_FOOT)
            mode_lbl = (
                {"modo_a_pe": "ON FOOT", "modo_veiculo": "VEHICLE", "modo_voo": "FLIGHT"}.get(mode_id, "ON FOOT")
                if is_en
                else st.get("modo_nome", "MODO A PÉ")
            )
            aim_tag = ("  [AIM]" if is_en else "  [MIRA]") if st.get("l2_toggled") else ""
            sx = float(st.get("sens_x", 0.25))
            sy = float(st.get("sens_y", 0.22))
            main_txt = f"● {mode_lbl}{aim_tag}  |  Sens {sx:.2f}/{sy:.2f}"

        sub_txt = ""
        if st.get("uipi_warning"):
            sub_txt = "⚠ UIPI: Chiaki is running as Admin!" if is_en else "⚠ UIPI: Chiaki está como Admin!"
            accent = ACCENT_WARN
        elif st.get("toast"):
            sub_txt = str(st["toast"])

        key = (accent, main_txt, sub_txt)
        if key == self._last_key:
            return
        self._last_key = key

        self._outer.config(highlightbackground=accent)
        self._strip.config(bg=accent)
        self._main_lbl.config(text=main_txt, fg="#ffffff" if st.get("ativo", True) else "#d0bfff")
        if sub_txt:
            self._sub_lbl.config(text=sub_txt)
            if not self._sub_visible:
                self._sub_lbl.pack(anchor="w")
                self._sub_visible = True
        elif self._sub_visible:
            self._sub_lbl.pack_forget()
            self._sub_visible = False
        self._reposition()


# ===========================================================================
# Aplicação principal do Editor Visual
# ===========================================================================

class ControllerEditorApp:
    """Editor gráfico com controle PlayStation clicável + painel de calibração."""

    def __init__(self, root: tk.Tk, mapping_path: Path = DEFAULT_MAPPING_FILE) -> None:
        self.root = root
        self.mapping_path = mapping_path
        self._profiles_dir = get_app_dir() / "perfis"
        self.data: dict[str, Any] = self._load_json_data()
        raw_lang = str(self.data.get("ajustes", {}).get("idioma", "pt")).strip().lower()
        self.lang: str = raw_lang if raw_lang in I18N else "pt"
        self.current_mode: str = "modo_a_pe"  # modo_a_pe | modo_veiculo | modo_voo | globais
        self.dirty: bool = False
        self._engine: GtaKeyboardMouseApp | None = None
        self._poll_job: str | None = None
        self._poll_tick: int = 0
        self._overlay: MiniOverlayHUD | None = None

        self.root.title(self._t("win_title"))
        self.root.configure(bg=BG_ROOT)
        try:
            ico_file = get_asset_path("assets/controle_ps5.ico")
            if ico_file.exists():
                self.root.iconbitmap(default=str(ico_file))
        except Exception:
            pass
        # Tamanho pensado para caber com folga em telas de notebook 1366x768
        self.root.geometry("1160x685")
        self.root.minsize(1060, 635)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._live_widgets: dict[str, dict[str, Any]] = {}
        self._lx_dot_id: int | None = None
        self._rx_dot_id: int | None = None
        self._last_live_active: set[str] = set()
        self._mode_buttons: dict[str, tk.Button] = {}
        self._slider_vars: dict[str, tk.DoubleVar] = {}
        self._slider_title_labels: dict[str, tk.Label] = {}
        self._bool_vars: dict[str, tk.BooleanVar] = {}
        self._bool_widgets: dict[str, tk.Checkbutton] = {}
        self._prof_btns: dict[str, tk.Button] = {}
        self._profile_var = tk.StringVar(value="Ativo (mapeamento.json)")

        self._build_layout()
        self._refresh_profile_selector()
        self._sync_controls_from_data()
        self._apply_language()
        self._update_status(self._t("status_ready"), FG_MUTED)

    def _t(self, key: str) -> str:
        return I18N.get(self.lang, I18N["pt"]).get(key, key)

    def toggle_language(self) -> None:
        self.lang = "en" if self.lang == "pt" else "pt"
        self.data["ajustes"]["idioma"] = self.lang
        self.dirty = True
        self._apply_language()
        self._update_status(
            "Language switched to English (click Save to persist)."
            if self.lang == "en"
            else "Idioma alterado para Português (clique em Salvar para manter).",
            ACCENT_FOOT,
        )

    def _apply_language(self) -> None:
        self.root.title(self._t("win_title"))
        if hasattr(self, "_header_title_lbl"):
            self._header_title_lbl.config(text=self._t("header_title"))
        if hasattr(self, "_prof_lbl"):
            self._prof_lbl.config(text=self._t("profile_lbl"))
        for k, btn in self._prof_btns.items():
            btn.config(text=self._t(k))
        if hasattr(self, "_lang_btn"):
            self._lang_btn.config(text=self._t("lang_btn"))

        tab_keys = {
            "modo_a_pe": "tab_foot",
            "modo_veiculo": "tab_vehicle",
            "modo_voo": "tab_flight",
            "globais": "tab_globals",
        }
        for mid, tkey in tab_keys.items():
            if mid in self._mode_buttons:
                self._mode_buttons[mid].config(text=self._t(tkey))

        if hasattr(self, "_sidebar_title_lbl"):
            self._sidebar_title_lbl.config(text=self._t("sidebar_title"))

        sl_keys = {
            "sensibilidade_x": "sl_sens_x",
            "sensibilidade_y": "sl_sens_y",
            "mult_mira_ads": "sl_ads",
            "deadzone_compensacao": "sl_dz",
            "suavizacao": "sl_smooth",
            "timeout_centro_ms": "sl_timeout",
            "tempo_rampa_r2_ms": "sl_r2",
            "tempo_rampa_direcao_lx_ms": "sl_lx",
        }
        for skey, tkey in sl_keys.items():
            if skey in self._slider_title_labels:
                self._slider_title_labels[skey].config(text=self._t(tkey))

        cb_keys = {
            "inverter_y": "cb_inv_y",
            "usar_captura_cursor_trackpad": "cb_trackpad",
            "apenas_quando_chiaki_em_foco": "cb_focus",
            "bloquear_entradas_no_windows": "cb_block",
            "feedback_sonoro": "cb_beep",
            "mostrar_mini_overlay": "cb_overlay",
        }
        for bkey, tkey in cb_keys.items():
            if bkey in self._bool_widgets:
                self._bool_widgets[bkey].config(text=self._t(tkey))

        if hasattr(self, "_diag_btn"):
            self._diag_btn.config(text=self._t("btn_diag"))
        if hasattr(self, "_reset_btn"):
            self._reset_btn.config(text=self._t("btn_reset"))
        if hasattr(self, "_save_btn"):
            self._save_btn.config(text=self._t("btn_save"))
        if hasattr(self, "_engine_btn"):
            running = self._engine is not None and self._engine.is_running
            self._engine_btn.config(text=self._t("btn_stop") if running else self._t("btn_start"))
        if hasattr(self, "_live_banner") and (self._engine is None or not self._engine.is_running):
            self._live_banner.config(text=self._t("banner_stopped"))

        self._refresh_health_badge()
        self._redraw_controller()

    # -- Persistência e Gerenciador de Perfis -------------------------------

    def _merge_loaded_json(self, loaded: dict[str, Any]) -> dict[str, Any]:
        base = copy.deepcopy(DEFAULT_JSON_DATA)
        for section in ("mouse_e_trackpad", "ajustes", "controles_sistema"):
            if isinstance(loaded.get(section), dict):
                base[section].update(loaded[section])
        for section in ("globais", "modo_a_pe", "modo_veiculo", "modo_voo"):
            if isinstance(loaded.get(section), dict):
                base[section] = dict(loaded[section])
        return base

    def _load_json_data(self) -> dict[str, Any]:
        base = copy.deepcopy(DEFAULT_JSON_DATA)
        if not self.mapping_path.exists():
            return base
        try:
            loaded = json.loads(self.mapping_path.read_text(encoding="utf-8"))
            return self._merge_loaded_json(loaded)
        except Exception as exc:
            messagebox.showwarning(
                "Aviso ao ler mapeamento.json",
                f"Não foi possível ler {self.mapping_path.name} ({exc}).\n"
                "Carregando perfil padrão.",
            )
            return base

    def _list_saved_custom_profiles(self) -> list[str]:
        if not self._profiles_dir.exists():
            return []
        return sorted(p.stem for p in self._profiles_dir.glob("*.json"))

    def _refresh_profile_selector(self) -> None:
        if not hasattr(self, "_profile_combo"):
            return
        values = ["Ativo (mapeamento.json)", *BUILTIN_PROFILES.keys()]
        for custom in self._list_saved_custom_profiles():
            label = f"Salvo: {custom}"
            if label not in values:
                values.append(label)
        self._profile_combo["values"] = values

    def apply_named_profile(self, choice: str) -> None:
        """Carrega um perfil pronto (GTA / RDR2 / FPS) ou salvo em `perfis/`."""
        if choice == "Ativo (mapeamento.json)":
            self.data = self._load_json_data()
        elif choice in BUILTIN_PROFILES:
            self.data = copy.deepcopy(BUILTIN_PROFILES[choice])
        elif choice.startswith("Salvo: "):
            stem = choice[len("Salvo: "):]
            path = self._profiles_dir / f"{stem}.json"
            if not path.exists():
                return
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.data = self._merge_loaded_json(loaded)
        else:
            return
        self._sync_controls_from_data()
        self._redraw_controller()
        self.dirty = True
        self._update_status(f"Perfil '{choice}' carregado (clique em Salvar para ativar).", ACCENT_FOOT)

    def save_profile_as(self) -> None:
        name = simpledialog.askstring(
            "Salvar Perfil Como",
            "Nome do perfil (ex.: GTA_Notebook, RDR2_Cavalo, COD_PS5):",
            parent=self.root,
        )
        if not name:
            return
        safe = "".join(ch for ch in name.strip() if ch.isalnum() or ch in ("_", "-", " ")).strip().replace(" ", "_")
        if not safe:
            messagebox.showwarning("Nome inválido", "Use letras, números, espaço ou underline.")
            return
        self._collect_panel_values_into_data()
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._profiles_dir / f"{safe}.json"
        out_path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._refresh_profile_selector()
        self._profile_var.set(f"Salvo: {safe}")
        self._update_status(f"Perfil salvo em perfis/{safe}.json!", ACCENT_OK)

    def import_profile(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Importar Perfil (.json)",
            filetypes=[("Perfis JSON", "*.json"), ("Todos os arquivos", "*.*")],
        )
        if not selected:
            return
        try:
            src = Path(selected)
            load_config_from_json(src)
            loaded = json.loads(src.read_text(encoding="utf-8"))
            self.data = self._merge_loaded_json(loaded)
            self._sync_controls_from_data()
            self._redraw_controller()
            self.dirty = True
            self._profile_var.set(f"Importado: {src.name}")
            self._update_status(f"Perfil '{src.name}' importado! Clique em Salvar para gravar.", ACCENT_OK)
        except Exception as exc:
            messagebox.showerror("Erro ao importar", f"Arquivo inválido ou incompatível:\n{exc}")

    def export_profile(self) -> None:
        self._collect_panel_values_into_data()
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Exportar Perfil (.json)",
            defaultextension=".json",
            initialfile="meu_perfil_ps5.json",
            filetypes=[("Perfis JSON", "*.json")],
        )
        if not target:
            return
        try:
            Path(target).write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self._update_status(f"Perfil exportado para {Path(target).name}!", ACCENT_OK)
        except Exception as exc:
            messagebox.showerror("Erro ao exportar", f"Não foi possível salvar o arquivo:\n{exc}")

    def _collect_panel_values_into_data(self) -> None:
        m = self.data["mouse_e_trackpad"]
        a = self.data["ajustes"]
        m["sensibilidade_x"] = round(self._slider_vars["sensibilidade_x"].get(), 3)
        m["sensibilidade_y"] = round(self._slider_vars["sensibilidade_y"].get(), 3)
        m["mult_mira_ads"] = round(self._slider_vars["mult_mira_ads"].get(), 2)
        m["deadzone_compensacao"] = round(self._slider_vars["deadzone_compensacao"].get(), 2)
        m["suavizacao"] = round(self._slider_vars["suavizacao"].get(), 2)
        m["timeout_centro_ms"] = round(self._slider_vars["timeout_centro_ms"].get(), 1)
        a["tempo_rampa_r2_ms"] = round(self._slider_vars["tempo_rampa_r2_ms"].get(), 1)
        a["tempo_rampa_direcao_lx_ms"] = round(self._slider_vars["tempo_rampa_direcao_lx_ms"].get(), 1)

        m["inverter_y"] = bool(self._bool_vars["inverter_y"].get())
        m["usar_captura_cursor_trackpad"] = bool(self._bool_vars["usar_captura_cursor_trackpad"].get())
        a["apenas_quando_chiaki_em_foco"] = bool(self._bool_vars["apenas_quando_chiaki_em_foco"].get())
        a["bloquear_entradas_no_windows"] = bool(self._bool_vars["bloquear_entradas_no_windows"].get())
        a["feedback_sonoro"] = bool(self._bool_vars["feedback_sonoro"].get())
        a["mostrar_mini_overlay"] = bool(self._bool_vars["mostrar_mini_overlay"].get())
        a["idioma"] = self.lang

    def apply_camera_preset(self, preset_id: str) -> None:
        """Aplica um preset pronto de Mouse Gamer / Mouse 125Hz / Trackpad em 1 clique."""
        preset = CAMERA_PRESETS.get(preset_id)
        if not preset:
            return
        self.data["mouse_e_trackpad"].update(preset["mouse_e_trackpad"])
        self._sync_controls_from_data()
        self.dirty = True
        self._update_status(
            f"Preset '{preset['label']}' aplicado ({preset['desc']}) — clique em Salvar.",
            ACCENT_FOOT,
        )

    def save_mapping(self) -> bool:
        self._collect_panel_values_into_data()
        try:
            text = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
            self.mapping_path.write_text(text, encoding="utf-8")
            # Valida imediatamente com o parser real do gta_controle.py
            load_config_from_json(self.mapping_path)
            self.dirty = False
            self._update_status(f"Salvo e validado em {self.mapping_path.name}!", ACCENT_OK)
            return True
        except Exception as exc:
            messagebox.showerror("Erro ao salvar", f"Falha ao validar/salvar mapeamento:\n{exc}")
            return False

    def toggle_engine(self) -> None:
        """Inicia ou para o controle virtual DS4 diretamente dentro deste executável."""
        if self._engine is not None and self._engine.is_running:
            self.stop_engine()
            return

        if not self.save_mapping():
            return

        probe = probe_vigembus_driver()
        if not probe.interface_present:
            open_dl = messagebox.askyesno(
                "Driver ViGEmBus 1.22.0 necessário",
                "O driver virtual ViGEmBus não está instalado/ativo neste Windows.\n\n"
                "Deseja abrir a página oficial do ViGEmBus 1.22.0 (WHQL) no navegador "
                "para instalar uma única vez?",
            )
            if open_dl:
                webbrowser.open(VIGEMBUS_RELEASE_URL)
            return

        try:
            cfg = load_config_from_json(self.mapping_path)
            engine = GtaKeyboardMouseApp(
                config=cfg,
                mapping_path=self.mapping_path,
                simulate=False,
                headless=True,
            )
            engine.start_background()
            self._engine = engine
            self._poll_tick = 0
            self._sync_overlay_visibility()
            self._engine_btn.config(
                text="⏹ Parar Controle Virtual",
                bg=ACCENT_DANGER,
                activebackground="#c92a2a",
            )
            self._update_status(
                "CONTROLE VIRTUAL ATIVO! Clique no Chiaki-ng p/ jogar (F12 pausa | F2 Voo | F7/F8 Sens).",
                ACCENT_OK,
            )
            self._schedule_engine_poll()
        except (DriverUnavailableError, BackendDependencyError, Exception) as exc:
            messagebox.showerror(
                "Falha ao iniciar controle virtual",
                f"Não foi possível iniciar o controle virtual DS4:\n\n{exc}",
            )

    def _sync_overlay_visibility(self) -> None:
        want = bool(self._bool_vars.get("mostrar_mini_overlay") and self._bool_vars["mostrar_mini_overlay"].get())
        running = self._engine is not None and self._engine.is_running
        if want and running:
            if self._overlay is None:
                try:
                    self._overlay = MiniOverlayHUD(self.root)
                except Exception:
                    self._overlay = None
        else:
            self._destroy_overlay()

    def _destroy_overlay(self) -> None:
        if self._overlay is not None:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None

    def stop_engine(self) -> None:
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

        self._destroy_overlay()
        if self._engine is not None:
            try:
                self._engine.stop_background()
            except Exception:
                pass
            self._engine = None

        self._clear_live_canvas()
        if hasattr(self, "_engine_btn"):
            self._engine_btn.config(
                text="▶ Salvar e Iniciar Controle",
                bg=ACCENT_OK,
                activebackground="#2b8a3e",
            )
        if hasattr(self, "_live_banner"):
            self._live_banner.config(
                text="MOTOR PARADO — Edite seu mapeamento acima e clique em '▶ Salvar e Iniciar Controle'.",
                fg=FG_MUTED,
                bg=BG_CARD,
            )
        self._update_status("Controle virtual encerrado com segurança (todos os comandos zerados).", FG_MUTED)

    def _schedule_engine_poll(self) -> None:
        if self._engine is None:
            return
        st = self._engine.get_live_status()
        if not st["running"]:
            err = st.get("error")
            self.stop_engine()
            if err:
                self._update_status(f"Motor interrompido: {err}", ACCENT_DANGER)
            return

        # 1. Preview ao vivo no controle visual (~28 FPS)
        self._update_live_canvas(st)

        # 2. Mini-Overlay flutuante click-through
        if self._overlay is not None:
            try:
                self._overlay.update_from_status(st, lang=self.lang)
                if (self._poll_tick % 30) == 15:
                    self._overlay.ensure_topmost()
            except Exception:
                self._destroy_overlay()

        # 3. Sincroniza sliders caso o usuário use F7/F8 ao vivo
        sx = round(float(st.get("sens_x", 0.25)), 3)
        sy = round(float(st.get("sens_y", 0.22)), 3)
        if abs(self._slider_vars["sensibilidade_x"].get() - sx) > 1e-4:
            self._slider_vars["sensibilidade_x"].set(sx)
            self._slider_vars["sensibilidade_x"]._val_label.config(text=f"{sx:.2f}")  # type: ignore[attr-defined]
            self.data["mouse_e_trackpad"]["sensibilidade_x"] = sx
        if abs(self._slider_vars["sensibilidade_y"].get() - sy) > 1e-4:
            self._slider_vars["sensibilidade_y"].set(sy)
            self._slider_vars["sensibilidade_y"]._val_label.config(text=f"{sy:.2f}")  # type: ignore[attr-defined]
            self.data["mouse_e_trackpad"]["sensibilidade_y"] = sy

        # 4. Faixa textual de telemetria (atualiza ~5x/s para economizar CPU)
        self._poll_tick += 1
        if (self._poll_tick % 6) == 1:
            is_en = self.lang == "en"
            ativo = ("ACTIVE" if st["ativo"] else "PAUSED (F12)") if is_en else ("ATIVO" if st["ativo"] else "PAUSADO (F12)")
            foco = (
                f"REMOTE PLAY ({st['proc_name']})"
                if st["chiaki_focused"]
                else (
                    "GLOBAL"
                    if not st["apenas_chiaki"]
                    else (
                        f"Waiting Remote Play focus ({st['proc_name']})"
                        if is_en
                        else f"Aguardando foco Remote Play ({st['proc_name']})"
                    )
                )
            )
            uipi_note = (" | ⚠ UIPI BLOCK (Admin!)" if is_en else " | ⚠ BLOQUEIO UIPI (Admin!)") if st["uipi_warning"] else ""
            cam_lbl = "Camera" if is_en else "Câmera"
            banner_txt = (
                f"● {ativo} | {st['modo_nome']} | {foco} | "
                f"{cam_lbl}: {st['mouse_source']} (Raw:{st['raw_packets']} / Track:{st['trackpad_packets']}) | "
                f"{st['actual_hz']:.0f} Hz{uipi_note}"
            )
            bg = "#3b2314" if st["uipi_warning"] else ("#132a1d" if st["ativo"] else "#2b233d")
            fg = "#ffd43b" if st["uipi_warning"] else ("#69db7c" if st["ativo"] else "#d0bfff")
            self._live_banner.config(text=banner_txt, fg=fg, bg=bg)

        self._poll_job = self.root.after(35, self._schedule_engine_poll)

    def open_diagnostics_dialog(self) -> None:
        """Abre janela com Semáforo de Latência visual + diagnóstico técnico em 4 camadas."""
        win = tk.Toplevel(self.root)
        win.title(self._t("diag_title"))
        win.configure(bg=BG_PANEL)
        win.geometry("820x600")
        win.transient(self.root)

        top_hdr = tk.Frame(win, bg=BG_PANEL, padx=14, pady=8)
        top_hdr.pack(fill="x")
        tk.Label(
            top_hdr,
            text=self._t("diag_sec_health"),
            font=("Segoe UI", 10, "bold"),
            fg=FG_TEXT,
            bg=BG_PANEL,
        ).pack(side="left")

        cards_frame = tk.Frame(win, bg=BG_PANEL, padx=14)
        cards_frame.pack(fill="x", pady=(0, 6))

        txt = scrolledtext.ScrolledText(
            win,
            font=("Consolas", 8),
            bg=BG_ROOT,
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            relief="flat",
            height=11,
            padx=10,
            pady=8,
        )
        txt.pack(fill="both", expand=True, padx=14, pady=(4, 6))

        def _populate() -> None:
            for w in cards_frame.winfo_children():
                w.destroy()
            items = run_latency_health_check()
            suf = "en" if self.lang == "en" else "pt"
            colors = {
                "green": ("● OK", "#69db7c", "#132a1d"),
                "yellow": ("▲ TIP" if suf == "en" else "▲ DICA", "#ffd43b", "#3b2c12"),
                "red": ("✖ ERR" if suf == "en" else "✖ ERRO", "#ff8787", "#3b1d24"),
            }
            for it in items:
                pill_txt, fg_c, bg_c = colors.get(it["status"], colors["yellow"])
                row = tk.Frame(cards_frame, bg=BG_CARD, padx=8, pady=4)
                row.pack(fill="x", pady=2)
                tk.Label(
                    row,
                    text=pill_txt,
                    font=("Segoe UI", 8, "bold"),
                    fg=fg_c,
                    bg=bg_c,
                    width=8,
                    pady=2,
                ).pack(side="left", padx=(0, 8))
                info_col = tk.Frame(row, bg=BG_CARD)
                info_col.pack(side="left", fill="x", expand=True)
                tk.Label(
                    info_col,
                    text=f"{it[f'title_{suf}']}  —  {it[f'detail_{suf}']}",
                    font=("Segoe UI", 8, "bold"),
                    fg=FG_TEXT,
                    bg=BG_CARD,
                    anchor="w",
                ).pack(anchor="w")
                if it["status"] != "green":
                    tk.Label(
                        info_col,
                        text=f"→ {it[f'tip_{suf}']}",
                        font=("Segoe UI", 8),
                        fg=fg_c,
                        bg=BG_CARD,
                        anchor="w",
                    ).pack(anchor="w")

            active_ctrl = self._engine.controller if self._engine is not None else None
            report = format_environment_report(active_controller=active_ctrl)
            uipi = check_chiaki_uipi_mismatch()
            if uipi:
                report = report + f"\n\n[ALERTA UIPI]\n{uipi}\n"
            txt.config(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", report)
            txt.config(state="disabled")
            self._refresh_health_badge()

        tk.Button(
            top_hdr,
            text=self._t("diag_refresh"),
            font=("Segoe UI", 8, "bold"),
            bg=BG_CARD,
            fg="#74c0fc",
            activebackground=BG_CTRL_BTN,
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=_populate,
        ).pack(side="right")

        _populate()

        bar = tk.Frame(win, bg=BG_PANEL, padx=14, pady=8)
        bar.pack(fill="x")
        tk.Button(
            bar,
            text=self._t("diag_download"),
            font=("Segoe UI", 9),
            bg=BG_CARD,
            fg="#74c0fc",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=lambda: webbrowser.open(VIGEMBUS_RELEASE_URL),
        ).pack(side="left")
        tk.Button(
            bar,
            text=self._t("diag_close"),
            font=("Segoe UI", 9, "bold"),
            bg=ACCENT_FOOT,
            fg="#ffffff",
            relief="flat",
            padx=14,
            pady=5,
            cursor="hand2",
            command=win.destroy,
        ).pack(side="right")

    def _on_close(self) -> None:
        self.stop_engine()
        self.root.destroy()

    def restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "Restaurar Padrão GTA",
            "Deseja restaurar todas as teclas e sensibilidades para o perfil padrão GTA?",
        ):
            return
        self.data = copy.deepcopy(DEFAULT_JSON_DATA)
        self._sync_controls_from_data()
        self._redraw_controller()
        self.dirty = True
        self._update_status("Perfil padrão carregado (clique em Salvar para gravar).", ACCENT_WARN)

    # -- Consultas e mutações de mapeamento ---------------------------------

    @staticmethod
    def _action_matches(stored_action: str, ui_action: str) -> bool:
        if ui_action == "R2":
            return stored_action in ("R2", "R2_RAMPA")
        if ui_action == "L2":
            return stored_action in ("L2", "L2_RAMPA", "L2_TOGGLE")
        if ui_action == "CROSS":
            return stored_action in ("CROSS", "CROSS_TAP")
        return stored_action == ui_action

    def _keys_for_action(self, section: str, action: str) -> list[str]:
        sec = self.data.get(section, {})
        return [k for k, v in sec.items() if self._action_matches(v, action)]

    def _inherited_global_keys_for_action(self, section: str, action: str) -> list[str]:
        """Teclas de `globais` que continuam ativas neste modo (não sobrescritas)."""
        if section == "globais":
            return []
        sec = self.data.get(section, {})
        glob = self.data.get("globais", {})
        return [
            k
            for k, v in glob.items()
            if (k not in sec) and self._action_matches(v, action)
        ]

    def _effective_keys_for_action(self, action: str) -> tuple[list[str], list[str]]:
        """Retorna `(teclas_do_modo, teclas_herdadas_de_globais)` realmente ativas."""
        if self.current_mode == "globais":
            return self._keys_for_action("globais", action), []
        own = self._keys_for_action(self.current_mode, action)
        inherited = self._inherited_global_keys_for_action(self.current_mode, action)
        return own, inherited

    def _has_stored_action_in_current_mode(self, exact_action: str) -> bool:
        sec = self.data.get(self.current_mode, {})
        return any(v == exact_action for v in sec.values())

    def _variant_spec(self, section: str, action: str) -> tuple[str, str] | None:
        """Retorna `(acao_variante, texto_checkbox)` suportada para este botão/modo."""
        if action == "R2" and section in ("modo_veiculo", "modo_voo"):
            return ("R2_RAMPA", "Usar aceleração suave com rampa (~100ms) no R2")
        if action == "L2" and section in ("modo_veiculo", "modo_voo"):
            return ("L2_RAMPA", "Usar frenagem/redução suave com rampa (~100ms) no L2")
        if action == "L2" and section == "modo_a_pe":
            return ("L2_TOGGLE", "Alternar Mira (clique 1x trava/solta L2 — ótimo p/ Trackpad)")
        if action == "CROSS" and section in ("modo_a_pe", "modo_veiculo"):
            return ("CROSS_TAP", "Sprint GTA automático (auto-tap ✕ enquanto segura)")
        return None

    def _format_badge(self, action: str) -> tuple[str, bool]:
        own, inherited = self._effective_keys_for_action(action)
        combined = own + [k for k in inherited if k not in own]
        if not combined:
            return "—", False
        is_own = bool(own)
        shown = [pretty_key(k) for k in combined[:2]]
        label = "/".join(shown)
        if len(combined) > 2:
            label += f"+{len(combined) - 2}"
        if action == "R2" and self._has_stored_action_in_current_mode("R2_RAMPA"):
            label += " ~"
        elif action == "L2" and self._has_stored_action_in_current_mode("L2_RAMPA"):
            label += " ~"
        elif action == "L2" and self._has_stored_action_in_current_mode("L2_TOGGLE"):
            label += " ⇄"
        elif action == "CROSS" and self._has_stored_action_in_current_mode("CROSS_TAP"):
            label += " ⚡"
        if (not is_own) and self.current_mode != "globais":
            label = f"{label} [G]"
        return label, is_own

    def _edit_action(self, action: str) -> None:
        section = self.current_mode
        current_own = self._keys_for_action(section, action)
        inherited = self._inherited_global_keys_for_action(section, action)
        var_spec = self._variant_spec(section, action)
        allow_var = var_spec is not None
        var_action = var_spec[0] if var_spec else action
        var_lbl = var_spec[1] if var_spec else ""
        init_var = self._has_stored_action_in_current_mode(var_action) if allow_var else False

        mode_title = {
            "modo_a_pe": "MODO A PÉ",
            "modo_veiculo": "MODO VEÍCULO",
            "modo_voo": "MODO VOO",
            "globais": "GLOBAIS",
        }[section]
        desc = ACTION_DESCRIPTIONS.get(action, action)

        dlg = KeyCaptureDialog(
            self.root,
            title=f"Remapear ({mode_title})",
            action_label=f"{desc}  —  [{mode_title}]",
            current_keys=current_own,
            inherited_keys=inherited,
            allow_clear=True,
            allow_multi=True,
            allow_mouse=True,
            allow_variant_toggle=allow_var,
            variant_label=var_lbl,
            initial_variant=init_var,
        )
        self.root.wait_window(dlg)
        if dlg.result_mode == "cancel":
            return

        sec_map: dict[str, str] = self.data[section]
        target_action = var_action if (allow_var and dlg.use_variant.get()) else action

        if dlg.result_mode == "update_variant":
            for k in current_own:
                sec_map[k] = target_action
            self.dirty = True
            self._redraw_controller()
            self._update_status(
                f"Modo do botão atualizado em {desc} [{mode_title}] -> {ACTION_DESCRIPTIONS.get(target_action, target_action)} (não salvo).",
                self._mode_accent(),
            )
            return

        if dlg.result_mode == "remove_one" and dlg.result_key:
            sec_map.pop(dlg.result_key, None)
            self.dirty = True
            self._redraw_controller()
            self._update_status(
                f"Tecla {pretty_key(dlg.result_key)} removida de {desc} em [{mode_title}] (não salvo).",
                ACCENT_WARN,
            )
            return

        if dlg.result_mode == "clear":
            to_del = [k for k, v in sec_map.items() if self._action_matches(v, action)]
            for k in to_del:
                del sec_map[k]
            self.dirty = True
            self._redraw_controller()
            self._update_status(f"Teclas removidas de {desc} em [{mode_title}] (não salvo).", ACCENT_WARN)
            return

        new_key = dlg.result_key
        if not new_key:
            return

        if dlg.result_mode == "replace":
            to_del = [k for k, v in sec_map.items() if self._action_matches(v, action)]
            for k in to_del:
                del sec_map[k]
        elif allow_var:
            for k in current_own:
                sec_map[k] = target_action

        override_note = ""
        if section != "globais" and new_key in self.data.get("globais", {}):
            prev_glob = self.data["globais"][new_key]
            if not self._action_matches(prev_glob, action):
                override_note = f" (sobrescreve {prev_glob} global neste modo)"

        sec_map[new_key] = target_action
        self.dirty = True
        self._redraw_controller()
        self._update_status(
            f"Definido: {pretty_key(new_key)} -> {ACTION_DESCRIPTIONS.get(target_action, target_action)} "
            f"[{mode_title}]{override_note} (não salvo).",
            self._mode_accent(),
        )

    def _edit_system_key(self, field_key: str, label: str) -> None:
        current = self.data["controles_sistema"].get(field_key, "")
        dlg = KeyCaptureDialog(
            self.root,
            title="Atalho de Sistema",
            action_label=label,
            current_keys=[current] if current else [],
            allow_clear=False,
            allow_multi=False,
            allow_mouse=False,
        )
        self.root.wait_window(dlg)
        if dlg.result_mode in ("replace", "add") and dlg.result_key:
            if dlg.result_key.startswith(("MOUSE_", "SCROLL_")):
                messagebox.showinfo("Atalho de sistema", "Use uma tecla do teclado para controles de sistema.")
                return
            self.data["controles_sistema"][field_key] = dlg.result_key
            self.dirty = True
            self._redraw_controller()
            self._update_status(f"Atalho atualizado: {label} = {dlg.result_key} (não salvo).", ACCENT_GLOBAL)

    # -- Construção da interface --------------------------------------------

    def _build_layout(self) -> None:
        # Cabeçalho + Gerenciador de Perfis + 4 Abas de Modo + Seletor PT/EN
        header = tk.Frame(self.root, bg=BG_PANEL, padx=14, pady=8)
        header.pack(fill="x")

        title_box = tk.Frame(header, bg=BG_PANEL)
        title_box.pack(side="left")
        self._header_title_lbl = tk.Label(
            title_box,
            text=self._t("header_title"),
            font=("Segoe UI", 12, "bold"),
            fg=FG_TEXT,
            bg=BG_PANEL,
        )
        self._header_title_lbl.pack(anchor="w")

        # Barra compacta de Perfis (GTA / RDR2 / FPS / Salvar Como / Importar / Exportar)
        prof_row = tk.Frame(title_box, bg=BG_PANEL)
        prof_row.pack(anchor="w", pady=(3, 0))
        self._prof_lbl = tk.Label(prof_row, text=self._t("profile_lbl"), font=("Segoe UI", 8, "bold"), fg=FG_MUTED, bg=BG_PANEL)
        self._prof_lbl.pack(side="left", padx=(0, 4))
        self._profile_combo = ttk.Combobox(
            prof_row,
            textvariable=self._profile_var,
            state="readonly",
            width=24,
            font=("Segoe UI", 8),
        )
        self._profile_combo.pack(side="left", padx=(0, 5))
        self._profile_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.apply_named_profile(self._profile_var.get()),
        )
        for p_key, p_cmd in (
            ("save_as", self.save_profile_as),
            ("import", self.import_profile),
            ("export", self.export_profile),
        ):
            pbtn = tk.Button(
                prof_row,
                text=self._t(p_key),
                font=("Segoe UI", 8),
                bg=BG_CARD,
                fg="#c5d4ea",
                activebackground=BG_CTRL_BTN_HOVER,
                activeforeground="#ffffff",
                relief="flat",
                padx=7,
                pady=2,
                cursor="hand2",
                command=p_cmd,
            )
            pbtn.pack(side="left", padx=2)
            self._prof_btns[p_key] = pbtn

        tabs = tk.Frame(header, bg=BG_PANEL)
        tabs.pack(side="right")
        for mode_id, t_key in (
            ("modo_a_pe", "tab_foot"),
            ("modo_veiculo", "tab_vehicle"),
            ("modo_voo", "tab_flight"),
            ("globais", "tab_globals"),
        ):
            btn = tk.Button(
                tabs,
                text=self._t(t_key),
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                padx=10,
                pady=7,
                cursor="hand2",
                command=lambda m=mode_id: self.switch_mode(m),
            )
            btn.pack(side="left", padx=2)
            self._mode_buttons[mode_id] = btn

        self._lang_btn = tk.Button(
            tabs,
            text=self._t("lang_btn"),
            font=("Segoe UI", 8, "bold"),
            bg=BG_CARD,
            fg="#74c0fc",
            activebackground=BG_CTRL_BTN_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            padx=8,
            pady=7,
            cursor="hand2",
            command=self.toggle_language,
        )
        self._lang_btn.pack(side="left", padx=(6, 0))

        # Corpo central (Esquerda: Controle Visual | Direita: Sliders & Opções)
        body = tk.Frame(self.root, bg=BG_ROOT, padx=12, pady=10)
        body.pack(fill="both", expand=True)

        left_col = tk.Frame(body, bg=BG_ROOT)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self.canvas = tk.Canvas(
            left_col,
            width=740,
            height=455,
            bg=BG_ROOT,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        # Barra de atalhos especiais / sistema abaixo do controle
        sys_bar = tk.Frame(left_col, bg=BG_PANEL, padx=10, pady=8)
        sys_bar.pack(fill="x", pady=(6, 0))
        self._sys_bar_frame = sys_bar

        # Painel direito (Presets + Calibração Mouse/Trackpad + Rampas)
        right_col = tk.Frame(body, bg=BG_PANEL, width=360, padx=14, pady=8)
        right_col.pack(side="right", fill="y")
        right_col.pack_propagate(False)
        self._build_sidebar(right_col)

        # Faixa de telemetria ao vivo do motor integrado
        self._live_banner = tk.Label(
            self.root,
            text=self._t("banner_stopped"),
            font=("Segoe UI", 9, "bold"),
            fg=FG_MUTED,
            bg=BG_CARD,
            anchor="w",
            padx=16,
            pady=6,
        )
        self._live_banner.pack(fill="x", side="bottom")

        # Rodapé com ações principais + status
        footer = tk.Frame(self.root, bg=BG_PANEL, padx=16, pady=9)
        footer.pack(fill="x", side="bottom")

        self.status_label = tk.Label(
            footer,
            text="",
            font=("Segoe UI", 9),
            fg=FG_MUTED,
            bg=BG_PANEL,
        )
        self.status_label.pack(side="left")

        btn_box = tk.Frame(footer, bg=BG_PANEL)
        btn_box.pack(side="right")

        self._diag_btn = tk.Button(
            btn_box,
            text=self._t("btn_diag"),
            font=("Segoe UI", 9),
            bg=BG_CARD,
            fg="#74c0fc",
            activebackground=BG_CTRL_BTN,
            activeforeground=FG_TEXT,
            relief="flat",
            padx=10,
            pady=6,
            cursor="hand2",
            command=self.open_diagnostics_dialog,
        )
        self._diag_btn.pack(side="left", padx=4)

        self._reset_btn = tk.Button(
            btn_box,
            text=self._t("btn_reset"),
            font=("Segoe UI", 9),
            bg=BG_CARD,
            fg=FG_TEXT,
            activebackground=BG_CTRL_BTN,
            activeforeground=FG_TEXT,
            relief="flat",
            padx=10,
            pady=6,
            cursor="hand2",
            command=self.restore_defaults,
        )
        self._reset_btn.pack(side="left", padx=4)

        self._save_btn = tk.Button(
            btn_box,
            text=self._t("btn_save"),
            font=("Segoe UI", 9, "bold"),
            bg=ACCENT_FOOT,
            fg="#ffffff",
            activebackground="#1c7ed6",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.save_mapping,
        )
        self._save_btn.pack(side="left", padx=4)

        self._engine_btn = tk.Button(
            btn_box,
            text=self._t("btn_start"),
            font=("Segoe UI", 9, "bold"),
            bg=ACCENT_OK,
            fg="#ffffff",
            activebackground="#2b8a3e",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.toggle_engine,
        )
        self._engine_btn.pack(side="left", padx=(4, 0))

    def _build_sidebar(self, parent: tk.Frame) -> None:
        self._sidebar_title_lbl = tk.Label(
            parent,
            text=self._t("sidebar_title"),
            font=("Segoe UI", 10, "bold"),
            fg=FG_TEXT,
            bg=BG_PANEL,
        )
        self._sidebar_title_lbl.pack(anchor="w", pady=(0, 4))

        # Presets rápidos em 1 clique
        preset_row = tk.Frame(parent, bg=BG_PANEL)
        preset_row.pack(fill="x", pady=(0, 6))
        for idx, (pid, pinfo) in enumerate(CAMERA_PRESETS.items()):
            tk.Button(
                preset_row,
                text=pinfo["label"],
                font=("Segoe UI", 8, "bold"),
                bg=BG_CARD,
                fg="#74c0fc",
                activebackground=BG_CTRL_BTN_HOVER,
                activeforeground="#ffffff",
                relief="flat",
                padx=6,
                pady=4,
                cursor="hand2",
                command=lambda p=pid: self.apply_camera_preset(p),
            ).pack(side="left", expand=True, fill="x", padx=(0 if idx == 0 else 4, 0))

        slider_specs = [
            ("sensibilidade_x", "sl_sens_x", 0.02, 0.80, 0.01, "{:.2f}"),
            ("sensibilidade_y", "sl_sens_y", 0.02, 0.80, 0.01, "{:.2f}"),
            ("mult_mira_ads", "sl_ads", 0.50, 2.50, 0.05, "{:.2f}x"),
            ("deadzone_compensacao", "sl_dz", 0.00, 0.35, 0.01, "{:.2f}"),
            ("suavizacao", "sl_smooth", 0.00, 0.60, 0.02, "{:.2f}"),
            ("timeout_centro_ms", "sl_timeout", 15.0, 120.0, 5.0, "{:.0f} ms"),
            ("tempo_rampa_r2_ms", "sl_r2", 0.0, 300.0, 10.0, "{:.0f} ms"),
            ("tempo_rampa_direcao_lx_ms", "sl_lx", 0.0, 250.0, 10.0, "{:.0f} ms"),
        ]

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.Horizontal.TScale", background=BG_PANEL, troughcolor=BG_CARD)

        for key, tkey, lo, hi, step, fmt in slider_specs:
            row = tk.Frame(parent, bg=BG_PANEL)
            row.pack(fill="x", pady=1)

            top = tk.Frame(row, bg=BG_PANEL)
            top.pack(fill="x")
            t_lbl = tk.Label(top, text=self._t(tkey), font=("Segoe UI", 8), fg=FG_TEXT, bg=BG_PANEL)
            t_lbl.pack(side="left")
            self._slider_title_labels[key] = t_lbl
            val_lbl = tk.Label(top, text="", font=("Segoe UI", 8, "bold"), fg="#74c0fc", bg=BG_PANEL)
            val_lbl.pack(side="right")

            var = tk.DoubleVar(value=lo)
            self._slider_vars[key] = var

            def _make_cb(v: tk.DoubleVar, lbl: tk.Label, f: str, st: float) -> Any:
                def _on_move(_raw: str) -> None:
                    snapped = round(v.get() / st) * st
                    lbl.config(text=f.format(snapped))
                    self.dirty = True
                return _on_move

            scale = ttk.Scale(
                row,
                from_=lo,
                to=hi,
                variable=var,
                style="Dark.Horizontal.TScale",
                command=_make_cb(var, val_lbl, fmt, step),
            )
            scale.pack(fill="x")
            var._val_label = val_lbl  # type: ignore[attr-defined]
            var._val_fmt = fmt        # type: ignore[attr-defined]

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=5)

        bool_specs = [
            ("inverter_y", "cb_inv_y"),
            ("usar_captura_cursor_trackpad", "cb_trackpad"),
            ("apenas_quando_chiaki_em_foco", "cb_focus"),
            ("bloquear_entradas_no_windows", "cb_block"),
            ("feedback_sonoro", "cb_beep"),
            ("mostrar_mini_overlay", "cb_overlay"),
        ]
        for key, tkey in bool_specs:
            bvar = tk.BooleanVar(value=False)
            self._bool_vars[key] = bvar

            def _make_bool_cb(k: str) -> Any:
                def _cb() -> None:
                    self.dirty = True
                    if k == "mostrar_mini_overlay":
                        self._sync_overlay_visibility()
                return _cb

            cb = tk.Checkbutton(
                parent,
                text=self._t(tkey),
                variable=bvar,
                bg=BG_PANEL,
                fg=FG_TEXT,
                selectcolor=BG_CARD,
                activebackground=BG_PANEL,
                activeforeground=FG_TEXT,
                font=("Segoe UI", 8),
                pady=0,
                command=_make_bool_cb(key),
            )
            cb.pack(anchor="w", pady=0)
            self._bool_widgets[key] = cb

        # Cartão clicável do Semáforo de Latência (somente leitura)
        self._health_badge = tk.Button(
            parent,
            text="",
            font=("Segoe UI", 8, "bold"),
            justify="left",
            anchor="w",
            wraplength=325,
            relief="flat",
            padx=8,
            pady=6,
            cursor="hand2",
            command=self.open_diagnostics_dialog,
        )
        self._health_badge.pack(fill="x", side="bottom", pady=(6, 0))
        self._refresh_health_badge()

    def _refresh_health_badge(self) -> None:
        if not hasattr(self, "_health_badge"):
            return
        items = run_latency_health_check()
        reds = [it for it in items if it["status"] == "red"]
        yellows = [it for it in items if it["status"] == "yellow"]
        total = len(items)
        greens = total - len(reds) - len(yellows)
        is_en = self.lang == "en"

        if reds:
            suf = "en" if is_en else "pt"
            txt = (
                f"✖ Health: {len(reds)} critical issue(s) — {reds[0][f'title_{suf}']} (Click)"
                if is_en
                else f"✖ Semáforo: {len(reds)} erro(s) — {reds[0][f'title_{suf}']} (Clique p/ ver)"
            )
            self._health_badge.config(text=txt, fg="#ff8787", bg="#3b1d24", activebackground="#5c2430")
        elif yellows:
            suf = "en" if is_en else "pt"
            txt = (
                f"▲ Health: {greens}/{total} green ({len(yellows)} latency tip — Click)"
                if is_en
                else f"▲ Semáforo: {greens}/{total} verde ({len(yellows)} dica de latência — Clique)"
            )
            self._health_badge.config(text=txt, fg="#ffd43b", bg="#3b2c12", activebackground="#523e1a")
        else:
            txt = (
                f"● Latency Health: {greens}/{total} Green — Optimal setup (Click for details)"
                if is_en
                else f"● Semáforo de Latência: {greens}/{total} Verde — Tudo otimizado! (Clique)"
            )
            self._health_badge.config(text=txt, fg="#69db7c", bg="#132a1d", activebackground="#1b3d2a")

    def _sync_controls_from_data(self) -> None:
        m = self.data["mouse_e_trackpad"]
        a = self.data["ajustes"]
        values = {
            "sensibilidade_x": float(m.get("sensibilidade_x", 0.25)),
            "sensibilidade_y": float(m.get("sensibilidade_y", 0.22)),
            "mult_mira_ads": float(m.get("mult_mira_ads", 1.25)),
            "deadzone_compensacao": float(m.get("deadzone_compensacao", 0.18)),
            "suavizacao": float(m.get("suavizacao", 0.0)),
            "timeout_centro_ms": float(m.get("timeout_centro_ms", 45.0)),
            "tempo_rampa_r2_ms": float(a.get("tempo_rampa_r2_ms", 100.0)),
            "tempo_rampa_direcao_lx_ms": float(a.get("tempo_rampa_direcao_lx_ms", 90.0)),
        }
        for key, val in values.items():
            var = self._slider_vars[key]
            var.set(val)
            var._val_label.config(text=var._val_fmt.format(val))  # type: ignore[attr-defined]

        self._bool_vars["inverter_y"].set(bool(m.get("inverter_y", False)))
        self._bool_vars["usar_captura_cursor_trackpad"].set(bool(m.get("usar_captura_cursor_trackpad", True)))
        self._bool_vars["apenas_quando_chiaki_em_foco"].set(bool(a.get("apenas_quando_chiaki_em_foco", True)))
        self._bool_vars["bloquear_entradas_no_windows"].set(bool(a.get("bloquear_entradas_no_windows", True)))
        self._bool_vars["feedback_sonoro"].set(bool(a.get("feedback_sonoro", True)))
        self._bool_vars["mostrar_mini_overlay"].set(bool(a.get("mostrar_mini_overlay", True)))

    def switch_mode(self, mode_id: str) -> None:
        self.current_mode = mode_id
        self._redraw_controller()

    def _mode_accent(self) -> str:
        return {
            "modo_a_pe": ACCENT_FOOT,
            "modo_veiculo": ACCENT_VEHICLE,
            "modo_voo": ACCENT_FLIGHT,
            "globais": ACCENT_GLOBAL,
        }.get(self.current_mode, ACCENT_FOOT)

    def _update_status(self, text: str, color: str = FG_MUTED) -> None:
        self.status_label.config(text=text, fg=color)

    # -- Desenho vetorial interativo do controle PlayStation ----------------

    def _draw_rounded_rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        r: float,
        **kwargs: Any,
    ) -> int:
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kwargs)

    def _add_clickable_button(
        self,
        action: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        title: str,
        shape: str = "rect",
        accent_outline: str | None = None,
        title_color: str = FG_TEXT,
    ) -> None:
        c = self.canvas
        tag = f"act_{action}_{int(x1)}_{int(y1)}"
        key_txt, is_own = self._format_badge(action)
        accent = self._mode_accent()

        outline = accent_outline or (accent if is_own and key_txt != "—" else BORDER_SUBTLE)
        fill = "#243147" if (is_own and key_txt != "—") else BG_CTRL_BTN
        width = 2 if (is_own and key_txt != "—") else 1

        if shape == "oval":
            bg_id = c.create_oval(
                x1, y1, x2, y2, fill=fill, outline=outline, width=width, tags=(tag,)
            )
        else:
            bg_id = self._draw_rounded_rect(
                x1, y1, x2, y2, 10, fill=fill, outline=outline, width=width, tags=(tag,)
            )

        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        if title:
            c.create_text(
                cx, cy - 9, text=title, fill=title_color,
                font=("Segoe UI", 9, "bold"), tags=(tag,)
            )
            c.create_text(
                cx, cy + 9, text=key_txt,
                fill=FG_TEXT if key_txt != "—" else FG_MUTED,
                font=("Segoe UI", 8, "bold" if is_own else "normal"),
                tags=(tag,),
            )
        else:
            c.create_text(
                cx, cy, text=key_txt,
                fill=FG_TEXT if key_txt != "—" else FG_MUTED,
                font=("Segoe UI", 8, "bold" if is_own else "normal"),
                tags=(tag,),
            )

        bar_id: int | None = None
        if action in ("L2", "R2"):
            bar_id = c.create_rectangle(
                x1 + 6, y2 - 6, x1 + 6, y2 - 2,
                fill=ACCENT_LIVE_GLOW, outline="", tags=(tag,),
            )

        self._live_widgets[action] = {
            "bg_id": bg_id,
            "orig_fill": fill,
            "orig_outline": outline,
            "orig_width": width,
            "bar_id": bar_id,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        }

        def _on_enter(_e: Any, bid: int = bg_id, act: str = action) -> None:
            c.itemconfig(bid, fill=BG_CTRL_BTN_HOVER)
            c.config(cursor="hand2")
            k_txt, _ = self._format_badge(act)
            self._update_status(
                f"Clique para remapear: {ACTION_DESCRIPTIONS.get(act, act)}  (atual: {k_txt})",
                self._mode_accent(),
            )

        def _on_leave(_e: Any, bid: int = bg_id, orig: str = fill) -> None:
            c.itemconfig(bid, fill=orig)
            c.config(cursor="")

        c.tag_bind(tag, "<Enter>", _on_enter)
        c.tag_bind(tag, "<Leave>", _on_leave)
        c.tag_bind(tag, "<Button-1>", lambda _e, act=action: self._edit_action(act))

    def _update_live_canvas(self, st: dict[str, Any]) -> None:
        """Ilumina botões pressionados, barras L2/R2 e pontos LX/LY + RX/RY ao vivo."""
        c = self.canvas
        pressed_btns: set[str] = set(st.get("buttons", ()))
        dpad_name = str(st.get("dpad", "NONE"))
        lx = float(st.get("lx", 0.0))
        ly = float(st.get("ly", 0.0))
        rx = float(st.get("rx", 0.0))
        ry = float(st.get("ry", 0.0))
        l2 = float(st.get("l2", 0.0))
        r2 = float(st.get("r2", 0.0))

        active_actions: set[str] = set()
        for b in ("CROSS", "CIRCLE", "SQUARE", "TRIANGLE", "L1", "R1", "L3", "R3", "OPTIONS", "SHARE", "PS"):
            if b in pressed_btns:
                active_actions.add(b)
        if "TOUCHPAD" in pressed_btns:
            active_actions.add("TOUCHPAD_TAP")
            active_actions.add("TOUCHPAD_HOLD_500")
        if l2 >= 0.05:
            active_actions.add("L2")
        if r2 >= 0.05:
            active_actions.add("R2")
        if "UP" in dpad_name:
            active_actions.add("DPAD_UP")
        if "DOWN" in dpad_name:
            active_actions.add("DPAD_DOWN")
        if "LEFT" in dpad_name:
            active_actions.add("DPAD_LEFT")
        if "RIGHT" in dpad_name:
            active_actions.add("DPAD_RIGHT")
        if lx < -0.15:
            active_actions.add("LX-")
        elif lx > 0.15:
            active_actions.add("LX+")
        if ly < -0.15:
            active_actions.add("LY+")
        elif ly > 0.15:
            active_actions.add("LY-")

        # Atualiza somente itens cujo estado mudou
        changed = active_actions.symmetric_difference(self._last_live_active)
        for act in changed:
            info = self._live_widgets.get(act)
            if not info:
                continue
            if act in active_actions:
                c.itemconfig(info["bg_id"], fill="#1c5432", outline=ACCENT_LIVE_GLOW, width=2)
            else:
                c.itemconfig(
                    info["bg_id"],
                    fill=info["orig_fill"],
                    outline=info["orig_outline"],
                    width=info["orig_width"],
                )
        self._last_live_active = active_actions

        # Barras analógicas proporcionais L2 / R2 (mostra rampa ao vivo!)
        for trig_name, trig_val in (("L2", l2), ("R2", r2)):
            info = self._live_widgets.get(trig_name)
            if info and info.get("bar_id"):
                x1 = info["x1"] + 6
                max_w = (info["x2"] - info["x1"]) - 12
                c.coords(info["bar_id"], x1, info["y2"] - 6, x1 + max_w * max(0.0, min(1.0, trig_val)), info["y2"] - 2)

        # Pontos de posição ao vivo nos analógicos LX/LY e RX/RY
        if self._lx_dot_id is not None:
            cx, cy = 291.0 + lx * 42.0, 313.0 + ly * 42.0
            r = 6 if (abs(lx) > 0.04 or abs(ly) > 0.04) else 3
            col = ACCENT_LIVE_GLOW if r == 6 else BORDER_SUBTLE
            c.coords(self._lx_dot_id, cx - r, cy - r, cx + r, cy + r)
            c.itemconfig(self._lx_dot_id, fill=col)

        if self._rx_dot_id is not None:
            cx, cy = 449.0 + rx * 42.0, 313.0 + ry * 42.0
            r = 6 if (abs(rx) > 0.04 or abs(ry) > 0.04) else 3
            col = "#38d9a9" if r == 6 else BORDER_SUBTLE
            c.coords(self._rx_dot_id, cx - r, cy - r, cx + r, cy + r)
            c.itemconfig(self._rx_dot_id, fill=col)

    def _clear_live_canvas(self) -> None:
        if not self._live_widgets:
            return
        self._update_live_canvas({"buttons": (), "dpad": "NONE", "lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0, "l2": 0.0, "r2": 0.0})

    def _redraw_controller(self) -> None:
        accent = self._mode_accent()
        for mid, btn in self._mode_buttons.items():
            if mid == self.current_mode:
                btn.config(bg=accent, fg="#ffffff")
            else:
                btn.config(bg=BG_CARD, fg=FG_MUTED)

        c = self.canvas
        c.delete("all")
        self._live_widgets.clear()
        self._last_live_active.clear()

        # --- Silhueta ergonômica do controle DualSense / DualShock 4 ---
        # Punhos (esquerdo e direito)
        c.create_oval(72, 230, 225, 445, fill=BG_CTRL_BODY, outline=BORDER_SUBTLE, width=2)
        c.create_oval(515, 230, 668, 445, fill=BG_CTRL_BODY, outline=BORDER_SUBTLE, width=2)
        # Corpo central
        self._draw_rounded_rect(105, 118, 635, 345, 52, fill=BG_CTRL_BODY, outline=BORDER_SUBTLE, width=2)
        # Máscara interna suave para unir corpo e punhos sem linhas cruzadas
        self._draw_rounded_rect(112, 125, 628, 338, 46, fill=BG_CTRL_BODY, outline="")
        c.create_oval(80, 238, 218, 438, fill=BG_CTRL_BODY, outline="")
        c.create_oval(522, 238, 660, 438, fill=BG_CTRL_BODY, outline="")
        # Recorte inferior central
        c.create_arc(250, 295, 490, 415, start=0, extent=180, fill=BG_ROOT, outline=BORDER_SUBTLE, width=2)

        # Barra de LED (Lightbar) colorida conforme o modo ativo
        c.create_rectangle(272, 121, 468, 128, fill=accent, outline="")

        # --- Gatilhos e Ombros (L2 / L1 / R2 / R1) ---
        self._add_clickable_button("L2", 122, 22, 240, 64, "L2 (Gatilho)")
        self._add_clickable_button("L1", 122, 70, 240, 110, "L1")
        self._add_clickable_button("R2", 500, 22, 618, 64, "R2 (Gatilho)")
        self._add_clickable_button("R1", 500, 70, 618, 110, "R1")

        # --- Share, Touchpad (Toque + Segurar 500ms), Options ---
        self._add_clickable_button("SHARE", 215, 144, 268, 186, "Share")
        self._add_clickable_button("TOUCHPAD_TAP", 278, 136, 368, 206, "Touch Toque")
        self._add_clickable_button("TOUCHPAD_HOLD_500", 372, 136, 462, 206, "Touch 500ms")
        self._add_clickable_button("OPTIONS", 472, 144, 525, 186, "Opts")

        # --- D-Pad (4 direções clicáveis) ---
        c.create_text(182, 146, text="D-PAD", fill=FG_MUTED, font=("Segoe UI", 8, "bold"))
        self._add_clickable_button("DPAD_UP", 156, 158, 208, 194, "▲")
        self._add_clickable_button("DPAD_DOWN", 156, 232, 208, 268, "▼")
        self._add_clickable_button("DPAD_LEFT", 106, 195, 158, 231, "◄")
        self._add_clickable_button("DPAD_RIGHT", 206, 195, 258, 231, "►")

        # --- Botões de Face PlayStation (△ ○ ✕ □) ---
        self._add_clickable_button(
            "TRIANGLE", 530, 148, 586, 194, "△", shape="oval",
            accent_outline=PS_COLOR_TRIANGLE, title_color=PS_COLOR_TRIANGLE,
        )
        self._add_clickable_button(
            "CIRCLE", 584, 194, 640, 240, "○", shape="oval",
            accent_outline=PS_COLOR_CIRCLE, title_color=PS_COLOR_CIRCLE,
        )
        self._add_clickable_button(
            "CROSS", 530, 240, 586, 286, "✕", shape="oval",
            accent_outline=PS_COLOR_CROSS, title_color=PS_COLOR_CROSS,
        )
        self._add_clickable_button(
            "SQUARE", 476, 194, 532, 240, "□", shape="oval",
            accent_outline=PS_COLOR_SQUARE, title_color=PS_COLOR_SQUARE,
        )

        # --- Analógico Esquerdo (LY+, LY-, LX-, LX+ e centro L3) ---
        c.create_oval(224, 246, 358, 380, fill=BG_CTRL_INNER, outline=BORDER_SUBTLE, width=2)
        c.create_text(291, 394, text="ANALÓGICO ESQUERDO (LX/LY)", fill=FG_MUTED, font=("Segoe UI", 8, "bold"))
        self._add_clickable_button("LY+", 267, 252, 315, 284, "↑")
        self._add_clickable_button("LY-", 267, 342, 315, 374, "↓")
        self._add_clickable_button("LX-", 228, 297, 268, 329, "←")
        self._add_clickable_button("LX+", 314, 297, 354, 329, "→")
        self._add_clickable_button("L3", 270, 292, 312, 334, "L3", shape="oval")

        # --- Analógico Direito (Mouse / Trackpad + centro R3) ---
        c.create_oval(382, 246, 516, 380, fill=BG_CTRL_INNER, outline=accent, width=2)
        c.create_text(449, 264, text="MOUSE /", fill="#74c0fc", font=("Segoe UI", 8, "bold"))
        c.create_text(449, 277, text="TRACKPAD", fill="#74c0fc", font=("Segoe UI", 8, "bold"))
        c.create_text(449, 366, text="RX / RY (360°)", fill=FG_MUTED, font=("Segoe UI", 7, "bold"))
        c.create_text(449, 394, text="ANALÓGICO DIREITO (CÂMERA)", fill=FG_MUTED, font=("Segoe UI", 8, "bold"))
        self._add_clickable_button("R3", 426, 294, 472, 340, "R3", shape="oval")

        # Pontos indicadores ao vivo para LX/LY e RX/RY
        self._lx_dot_id = c.create_oval(288, 310, 294, 316, fill=BORDER_SUBTLE, outline="")
        self._rx_dot_id = c.create_oval(446, 310, 452, 316, fill=BORDER_SUBTLE, outline="")

        # Legenda no topo da tela
        mode_caption = {
            "modo_a_pe": self._t("cap_foot"),
            "modo_veiculo": self._t("cap_vehicle"),
            "modo_voo": self._t("cap_flight"),
            "globais": self._t("cap_globals"),
        }[self.current_mode]
        c.create_text(370, 428, text=mode_caption, fill=accent, font=("Segoe UI", 9, "bold"))

        # Barra inferior de atalhos especiais e de sistema
        for w in self._sys_bar_frame.winfo_children():
            w.destroy()

        sys_keys = self.data["controles_sistema"]
        lento_txt, _ = self._format_badge("MOD_LENTO")
        hab_txt, _ = self._format_badge("L3+R3")
        ps_txt, _ = self._format_badge("PS")

        pills = [
            (
                f"Lento: {lento_txt}",
                lambda: self._edit_action("MOD_LENTO"),
                BG_CARD,
            ),
            (
                f"L3+R3: {hab_txt}",
                lambda: self._edit_action("L3+R3"),
                BG_CARD,
            ),
            (
                f"PS: {ps_txt}",
                lambda: self._edit_action("PS"),
                BG_CARD,
            ),
            (
                f"Modo+△: {sys_keys.get('alternar_modo_e_triangulo', 'F')}",
                lambda: self._edit_system_key("alternar_modo_e_triangulo", "Alternar Modo + Triângulo"),
                "#1d2d44",
            ),
            (
                f"Modo: {sys_keys.get('alternar_modo_silencioso', 'F1')}",
                lambda: self._edit_system_key("alternar_modo_silencioso", "Alternar Modo Silencioso"),
                "#1d2d44",
            ),
            (
                f"Voo: {sys_keys.get('alternar_modo_voo', 'F2')}",
                lambda: self._edit_system_key("alternar_modo_voo", "Alternar Modo Voo / Aeronave"),
                "#123540",
            ),
            (
                f"Pausa: {sys_keys.get('pausar_ativar', 'F12')}",
                lambda: self._edit_system_key("pausar_ativar", "Pausar / Ativar Script"),
                "#2b233d",
            ),
        ]
        for text, cmd, bg in pills:
            tk.Button(
                self._sys_bar_frame,
                text=text,
                font=("Segoe UI", 8, "bold"),
                bg=bg,
                fg=FG_TEXT,
                activebackground=BG_CTRL_BTN_HOVER,
                activeforeground=FG_TEXT,
                relief="flat",
                padx=5,
                pady=5,
                cursor="hand2",
                command=cmd,
            ).pack(side="left", padx=2, expand=True, fill="x")


# ===========================================================================
# Autotestes do Editor Visual (`--self-test`)
# ===========================================================================

def run_self_tests() -> bool:
    checks: list[tuple[str, bool, str]] = []

    # 1. Tradução de keysyms Tkinter (incluindo ABNT2, Numpad, Alt_L) -> mapeamento.json
    pairs = [
        ("Shift_L", 0, "LSHIFT"),
        ("Control_L", 0, "LCTRL"),
        ("Alt_L", 18, "LALT"),
        ("space", 0, "SPACE"),
        ("Return", 0, "ENTER"),
        ("BackSpace", 0, "BACKSPACE"),
        ("Escape", 0, "ESC"),
        ("Up", 0, "UP"),
        ("F12", 0, "F12"),
        ("w", 0x57, "W"),
        ("ccedilla", 0xBA, "CEDILHA"),
        ("KP_4", 0x64, "NUM4"),
        ("KP_Add", 0x6B, "NUM_ADD"),
        ("comma", 0xBC, "COMMA"),
    ]
    bad = [(k, tk_event_to_key_name(k, c), exp) for k, c, exp in pairs if tk_event_to_key_name(k, c) != exp]
    checks.append(("Tradução de teclas Tk (ABNT2/Numpad/Alt) -> mapeamento.json", not bad, "OK" if not bad else str(bad)))

    # 2. Instanciação da janela visual + troca dos 4 modos + desenho + multi-tecla + presets + Fase 2
    root = tk.Tk()
    root.withdraw()
    try:
        app = ControllerEditorApp(root, mapping_path=DEFAULT_MAPPING_FILE)
        for mode in ("modo_a_pe", "modo_veiculo", "modo_voo", "globais", "modo_a_pe"):
            app.switch_mode(mode)
            root.update_idletasks()
        items_count = len(app.canvas.find_all())
        app._collect_panel_values_into_data()
        checks.append(
            (
                "Construção visual do controle PlayStation + 4 modos (incl. Voo) + 8 sliders",
                items_count > 65 and "tempo_rampa_direcao_lx_ms" in app.data["ajustes"],
                f"Canvas renderizado com {items_count} elementos vetoriais interativos.",
            )
        )

        # 3. Badges multi-tecla + Override Modo>Globais + Rampa L2/R2 + CROSS_TAP + L2_TOGGLE
        app.data["globais"] = {"A": "LX-", "LEFT": "DPAD_LEFT", "C": "R3"}
        app.data["modo_veiculo"] = {"LEFT": "LX-", "W": "R2_RAMPA", "S": "L2_RAMPA"}
        app.data["modo_a_pe"] = {"LSHIFT": "CROSS_TAP", "MOUSE_RIGHT": "L2_TOGGLE"}
        app.switch_mode("modo_veiculo")
        lx_badge, lx_own = app._format_badge("LX-")
        dpad_l_badge, _ = app._format_badge("DPAD_LEFT")
        r3_badge, r3_own = app._format_badge("R3")
        l2_badge, _ = app._format_badge("L2")
        app.switch_mode("modo_a_pe")
        cross_badge, _ = app._format_badge("CROSS")
        l2_tog_badge, _ = app._format_badge("L2")
        multi_badge_ok = (
            lx_badge == "←/A"
            and lx_own
            and dpad_l_badge == "—"
            and r3_badge == "C [G]"
            and (not r3_own)
            and l2_badge == "S ~"
            and cross_badge == "LShift ⚡"
            and l2_tog_badge == "M.Dir ⇄"
        )
        checks.append(
            (
                "Badges multi-tecla + Override + Rampa (~) + Sprint Tap (⚡) + Mira Toggle (⇄)",
                multi_badge_ok,
                f"LX-='{lx_badge}', DPAD_L='{dpad_l_badge}', CROSS='{cross_badge}', L2_A_PE='{l2_tog_badge}'",
            )
        )

        # 4. Presets de câmera 1-clique + Perfis prontos (RDR2 / FPS)
        app.apply_camera_preset("mouse_gamer")
        mg_ok = (
            abs(app._slider_vars["sensibilidade_x"].get() - 0.14) < 1e-6
            and not app._bool_vars["usar_captura_cursor_trackpad"].get()
        )
        app.apply_named_profile("Red Dead Redemption 2")
        rdr_ok = app.data["modo_a_pe"].get("LSHIFT") == "CROSS_TAP"
        app.apply_named_profile("Shooter / FPS (PS5)")
        fps_ok = app.data["modo_a_pe"].get("SPACE") == "CROSS"
        checks.append(
            (
                "Presets 1-clique + Perfis integrados (GTA / RDR2 / FPS)",
                mg_ok and rdr_ok and fps_ok,
                f"mouse_gamer={mg_ok}, rdr2={rdr_ok}, fps={fps_ok}",
            )
        )

        # 5. Preview ao vivo no controle visual + Mini-Overlay click-through
        app._update_live_canvas(
            {
                "buttons": {"CROSS", "L1", "R2"},
                "dpad": "UP_RIGHT",
                "lx": -0.8,
                "ly": -0.5,
                "rx": 0.6,
                "ry": -0.4,
                "l2": 0.5,
                "r2": 1.0,
            }
        )
        live_ok = ("CROSS" in app._last_live_active) and ("L2" in app._last_live_active) and ("LX-" in app._last_live_active)
        app._clear_live_canvas()
        ov = MiniOverlayHUD(root)
        ov.withdraw()
        ov.update_from_status(
            {"ativo": True, "modo_atual": "modo_voo", "modo_nome": "MODO VOO", "sens_x": 0.25, "sens_y": 0.22, "toast": "MODO VOO ATIVO"}
        )
        ov_ok = "MODO VOO" in ov._main_lbl.cget("text")
        ov.destroy()
        checks.append(
            (
                "Preview ao vivo no controle + Mini-Overlay click-through",
                live_ok and ov_ok,
                f"live_highlight={live_ok}, mini_overlay={ov_ok}",
            )
        )

        # 6. Fase 3: Troca instantânea PT/EN + Semáforo de Latência + Ícone .ico
        app.toggle_language()
        en_ok = (app.lang == "en") and ("ON FOOT" in app._mode_buttons["modo_a_pe"].cget("text"))
        app.toggle_language()
        pt_ok = (app.lang == "pt") and ("A PÉ" in app._mode_buttons["modo_a_pe"].cget("text"))
        health = run_latency_health_check()
        ico_ok = get_asset_path("assets/controle_ps5.ico").exists()
        checks.append(
            (
                "Fase 3: Troca PT/EN ao vivo + Semáforo de Latência + Ícone .ico",
                en_ok and pt_ok and bool(health) and ico_ok,
                f"en={en_ok}, pt={pt_ok}, health_items={len(health)}, ico={ico_ok}",
            )
        )
    finally:
        root.destroy()

    # 7. Integridade do mapeamento.json atual
    cfg = load_config_from_json(DEFAULT_MAPPING_FILE)
    checks.append(
        (
            "Validação do mapeamento.json compatível com gta_controle.py",
            bool(cfg.modo_a_pe and cfg.modo_veiculo and cfg.modo_voo and cfg.globais),
            f"globais={len(cfg.globais)}, a_pe={len(cfg.modo_a_pe)}, veiculo={len(cfg.modo_veiculo)}, voo={len(cfg.modo_voo)}",
        )
    )

    print("=" * 74)
    print("AUTOTESTES EDITOR VISUAL DO CONTROLE PLAYSTATION")
    print("=" * 74)
    for i, (name, ok, det) in enumerate(checks, 1):
        print(f"[{'PASS' if ok else 'FAIL'}] {i:02d}. {name}")
        print(f"       {det}")
    print("-" * 74)
    all_ok = all(ok for _, ok, _ in checks)
    print(f"RESUMO: {sum(ok for _, ok, _ in checks)}/{len(checks)} aprovados ({'SUCESSO' if all_ok else 'FALHA'}).")
    print("=" * 74)
    return all_ok


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    args = sys.argv[1:] if argv is None else argv
    if "--play" in args:
        import gta_controle  # noqa: PLC0415

        return gta_controle.main([a for a in args if a != "--play"])
    if "--diag" in args:
        import diagnostico  # noqa: PLC0415

        return diagnostico.main([a for a in args if a != "--diag"])
    if "--test-menu" in args:
        import main as test_menu  # noqa: PLC0415

        return test_menu.main([a for a in args if a != "--test-menu"])
    if "--self-test" in args:
        return 0 if run_self_tests() else 1

    root = tk.Tk()
    ControllerEditorApp(root, mapping_path=DEFAULT_MAPPING_FILE)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
