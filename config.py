"""Configurações centrais, constantes de protocolo DS4 e definições do projeto.

Responsabilidade:
    Centralizar parâmetros configuráveis, enums de comandos PlayStation,
    constantes de identificação de hardware/driver no Windows e caminhos padrão
    do Chiaki-ng, evitando números mágicos espalhados pela aplicação.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Metadados da aplicação
# ---------------------------------------------------------------------------

APP_NAME = "Teste de Compatibilidade — Controle Virtual PS (Chiaki-ng)"
APP_VERSION = "1.0.0-etapa1"


# ---------------------------------------------------------------------------
# Identificadores de hardware virtual (DualShock 4 USB HID via ViGEmBus)
# ---------------------------------------------------------------------------

DS4_VENDOR_ID = 0x054C   # Sony Interactive Entertainment
DS4_PRODUCT_ID = 0x05C4  # DualShock 4 (v1 wired — perfil padrão do ViGEmBus)

VIGEMBUS_SERVICE_NAME = "ViGEmBus"
VIGEMBUS_SYS_PATH = Path(r"C:\Windows\System32\drivers\ViGEmBus.sys")
VIGEMBUS_INTERFACE_GUID = "{96E42B22-F5E9-42F8-B043-ED0F932F014F}"
VIGEMBUS_RECOMMENDED_VERSION = "1.22.0"
VIGEMBUS_RELEASE_URL = "https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0"

HID_INTERFACE_GUID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
HID_CLASS_GUID = "{745A17A0-74D3-11D0-B6FE-00A0C90F57DA}"

HIDGUARDIAN_SERVICE_NAME = "HidGuardian"

CHIAKI_DEFAULT_PATHS: tuple[Path, ...] = (
    Path(r"C:\Program Files\chiaki-ng"),
    Path(r"C:\Program Files (x86)\chiaki-ng"),
)


# ---------------------------------------------------------------------------
# Enums dos comandos PlayStation suportados na Etapa 1
# ---------------------------------------------------------------------------

class PSButton(str, Enum):
    """Botões PlayStation suportados pelo controle virtual DualShock 4."""

    CROSS = "Cross"
    CIRCLE = "Circle"
    SQUARE = "Square"
    TRIANGLE = "Triangle"
    L1 = "L1"
    R1 = "R1"
    L2 = "L2"
    R2 = "R2"
    L3 = "L3"
    R3 = "R3"
    OPTIONS = "Options"
    SHARE = "Share"
    TOUCHPAD = "Touchpad"
    PS = "PS"


# Ordem de exibição e rótulos amigáveis em português
PS_BUTTON_LABELS: dict[PSButton, str] = {
    PSButton.CROSS: "Cruz (Cross)",
    PSButton.CIRCLE: "Círculo (Circle)",
    PSButton.SQUARE: "Quadrado (Square)",
    PSButton.TRIANGLE: "Triângulo (Triangle)",
    PSButton.L1: "L1",
    PSButton.R1: "R1",
    PSButton.L2: "L2 (Digital + Analógico)",
    PSButton.R2: "R2 (Digital + Analógico)",
    PSButton.L3: "L3 (Clique Analógico Esq.)",
    PSButton.R3: "R3 (Clique Analógico Dir.)",
    PSButton.OPTIONS: "Options",
    PSButton.SHARE: "Share / Create",
    PSButton.TOUCHPAD: "Clique do Touchpad",
    PSButton.PS: "Botão PS (Sistema)",
}

# Botões incluídos no teste automatizado completo (PS fica opcional no menu de
# botões individuais para evitar abrir o menu do sistema no PS5 no meio do teste)
AUTO_TEST_BUTTONS: tuple[PSButton, ...] = (
    PSButton.CROSS,
    PSButton.CIRCLE,
    PSButton.SQUARE,
    PSButton.TRIANGLE,
    PSButton.L1,
    PSButton.R1,
    PSButton.L2,
    PSButton.R2,
    PSButton.L3,
    PSButton.R3,
    PSButton.OPTIONS,
    PSButton.SHARE,
    PSButton.TOUCHPAD,
)


class DPadDirection(str, Enum):
    """Direções do D-Pad (HAT) do DualShock 4."""

    NONE = "NONE"
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UP_LEFT = "UP_LEFT"
    UP_RIGHT = "UP_RIGHT"
    DOWN_LEFT = "DOWN_LEFT"
    DOWN_RIGHT = "DOWN_RIGHT"


DPAD_CARDINAL_DIRECTIONS: tuple[DPadDirection, ...] = (
    DPadDirection.UP,
    DPadDirection.RIGHT,
    DPadDirection.DOWN,
    DPadDirection.LEFT,
)

DPAD_LABELS: dict[DPadDirection, str] = {
    DPadDirection.NONE: "Neutro (Solto)",
    DPadDirection.UP: "Cima (UP)",
    DPadDirection.DOWN: "Baixo (DOWN)",
    DPadDirection.LEFT: "Esquerda (LEFT)",
    DPadDirection.RIGHT: "Direita (RIGHT)",
    DPadDirection.UP_LEFT: "Cima-Esquerda (UP_LEFT)",
    DPadDirection.UP_RIGHT: "Cima-Direita (UP_RIGHT)",
    DPadDirection.DOWN_LEFT: "Baixo-Esquerda (DOWN_LEFT)",
    DPadDirection.DOWN_RIGHT: "Baixo-Direita (DOWN_RIGHT)",
}


class AnalogAxis(str, Enum):
    """Eixos analógicos independentes."""

    LX = "LX"
    LY = "LY"
    RX = "RX"
    RY = "RY"


AXIS_LABELS: dict[AnalogAxis, str] = {
    AnalogAxis.LX: "Analógico Esquerdo Horizontal (LX: -1.0 Esq / +1.0 Dir)",
    AnalogAxis.LY: "Analógico Esquerdo Vertical (LY: -1.0 Cima / +1.0 Baixo)",
    AnalogAxis.RX: "Analógico Direito Horizontal (RX: -1.0 Esq / +1.0 Dir)",
    AnalogAxis.RY: "Analógico Direito Vertical (RY: -1.0 Cima / +1.0 Baixo)",
}


class TriggerName(str, Enum):
    """Gatilhos analógicos independentes."""

    L2 = "L2"
    R2 = "R2"


# ---------------------------------------------------------------------------
# Parâmetros operacionais e sequências padrão de teste
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ControllerConfig:
    """Parâmetros de operação do loop de atualização e mapeamento HID.

    Notas técnicas sobre convenção de eixos (DS4 HID / SDL3 / vgamepad >= 0.1.0):
        - X: -1.0 = esquerda (byte 1), 0.0 = centro (byte 128), +1.0 = direita (byte 255)
        - Y: -1.0 = cima     (byte 1), 0.0 = centro (byte 128), +1.0 = baixo   (byte 255)
        - Gatilhos L2/R2: 0.0 = solto (byte 0), 1.0 = máximo (byte 255)
    """

    update_rate_hz: float = 100.0
    min_update_rate_hz: float = 10.0
    max_update_rate_hz: float = 250.0
    use_high_res_timer_windows: bool = True

    invert_y_axis: bool = False
    trigger_digital_threshold: float = 0.05
    touchpad_click_with_center_finger: bool = False
    touchpad_center_xy: tuple[int, int] = (960, 471)

    shutdown_reset_frames: int = 3
    shutdown_reset_interval_s: float = 0.015
    thread_join_timeout_s: float = 2.0

    monitor_refresh_hz: float = 12.0
    default_step_hold_s: float = 0.55
    default_step_pause_s: float = 0.25

    def validated_hz(self) -> float:
        """Retorna a taxa de atualização limitada ao intervalo seguro."""
        return max(self.min_update_rate_hz, min(self.max_update_rate_hz, float(self.update_rate_hz)))


DEFAULT_CONFIG = ControllerConfig()

# Sequência de valores para validação completa de cada eixo analógico:
# negativo máximo, negativo intermediário, centro, positivo intermediário, positivo máximo, centro.
AXIS_TEST_SEQUENCE: tuple[tuple[str, float], ...] = (
    ("Negativo máximo (-1.00)", -1.0),
    ("Negativo intermediário (-0.50)", -0.5),
    ("Retorno ao centro (0.00)", 0.0),
    ("Positivo intermediário (+0.50)", 0.5),
    ("Positivo máximo (+1.00)", 1.0),
    ("Retorno ao centro (0.00)", 0.0),
)

# Sequência de valores analógicos (não binários) para gatilhos L2 e R2.
TRIGGER_TEST_SEQUENCE: tuple[tuple[str, float], ...] = (
    ("Solto (0.00 / 0%)", 0.0),
    ("Intermediário baixo (0.25 / 25%)", 0.25),
    ("Intermediário médio (0.50 / 50%)", 0.50),
    ("Intermediário alto (0.75 / 75%)", 0.75),
    ("Máximo (1.00 / 100%)", 1.00),
    ("Retorno a zero (0.00 / 0%)", 0.0),
)
