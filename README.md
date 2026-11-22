# GTA PS5 — Controle Virtual (Teclado + Mouse/Trackpad → PS5 Remote Play)

> **PT-BR / EN** — Software open-source para Windows que converte **Teclado + Mouse ou Trackpad** em um **DualShock 4 virtual nativo (`VID_054C / PID_05C4`)** com baixa latência (~200 Hz), Editor Visual interativo, Perfis Multi-Jogo, Mini-Overlay *click-through* e Semáforo de Latência para **Chiaki-ng**, **PS Remote Play** e **PXPlay**.
>
> *Open-source Windows utility that converts PC Keyboard + Mouse/Trackpad into a native virtual DualShock 4 controller (~200 Hz) for PS5 Remote Play (Chiaki-ng, Sony PS Remote Play, PXPlay) with a Visual Controller Editor, Multi-Game Profiles, Click-Through HUD Overlay, and Read-Only Latency Health Check.*

---

## Principais Recursos (`v1.0.1`)

- **Controle Visual Interativo + Preview ao Vivo**: clique em qualquer botão, gatilho ou direção do PlayStation para remapear; enquanto o motor está ativo, botões, barras analógicas `L2/R2` e analógicos `LX/LY` + `RX/RY` acendem em tempo real.
- **4 Contextos Inteligentes (Prioridade Modo > Globais)**:
  - **MODO A PÉ**, **MODO VEÍCULO** (`F` / `F1`), **MODO VOO / AERONAVE** (`F2`) e **GLOBAIS**.
  - Múltiplas teclas para a mesma função (ex.: virar com `A`/`D` **e** com as setas `←`/`→`), com pílulas `[ Tecla × ]` para remover uma tecla individualmente.
- **Baixa Latência & Suporte Real a Trackpad de Notebook**:
  - Loop síncrono a 200 Hz (`timeBeginPeriod(1)` + envio direto sem double-buffering).
  - Captura híbrida: **Raw Input (`WM_INPUT`)** para Mouse + **Cursor Free-Box** não-destrutivo para Windows Precision Touchpad.
  - Presets prontos em 1 clique: **Mouse Gamer** | **Mouse 125Hz** | **Trackpad**.
- **Ergonomia PC para GTA & Jogos de PS5**:
  - `R2_RAMPA` e `L2_RAMPA` (~100 ms) + Rampa suave de direção `A/D` em veículos.
  - `CROSS_TAP` (`⚡`): auto-tap contínuo de `✕` para Sprint no GTA sem cansar o dedo.
  - `L2_TOGGLE` (`⇄`): alternar mira com 1 clique (ideal para quem joga no Trackpad).
  - `MOD_LENTO`: modificador de caminhada lenta (45%).
  - Suporte completo a `Mouse Meio`, `Mouse 4`, `Mouse 5`, `Scroll ↑/↓`, `Alt Esq.`, `Ç`, acentos ABNT2 e `Numpad 0–9`.
- **Mini-Overlay Flutuante *Click-Through***: pílula discreta sobre o jogo mostrando modo ativo, mira travada e sensibilidade ajustável ao vivo (`F7` / `F8`), sem roubar foco nem cliques.
- **Gerenciador de Perfis**: perfis prontos para **GTA V / VI**, **Red Dead Redemption 2** e **Shooter / FPS**, além de **Salvar Como**, **Importar** e **Exportar `.json`**.
- **Semáforo de Latência (Somente Leitura)**: verifica automaticamente ViGEmBus, UIPI (Admin), Keymap interno, FPS (60 Hz), VSync, Mouse Touchpad e decodificação por hardware do Chiaki-ng.
- **Foco Inteligente Multi-Cliente**: captura ativa apenas quando **Chiaki-ng**, **PS Remote Play** ou **PXPlay** está em foco (`Alt+Tab` libera mouse e teclado instantaneamente).
- **Bilíngue (`PT | EN`)**: troca instantânea de idioma na própria janela.

---

## Como Usar (Download Pronto em Releases)

1. **Instale o driver oficial ViGEmBus 1.22.0 (WHQL)** (uma única vez — compatível com Windows 11 e Integridade de Memória / HVCI):
   - Download oficial: https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0 (`ViGEmBus_1.22.0_x64_x86_arm64.exe`).
2. Baixe e extraia **`GTA_PS5_Controle_v1.0.1_Windows_x64.zip`** na aba **Releases** deste repositório.
3. Abra **`GTA_PS5_Controle_v1.0.1.exe`**.
   - *Nota sobre o Windows SmartScreen*: como o executável open-source não possui certificado pago de assinatura de código, o Windows pode exibir "O Windows protegeu o computador". Clique em **Mais informações → Executar assim mesmo**, ou confira o hash em `SHA256SUMS.txt` / compile você mesmo a partir do código-fonte abaixo.
4. Escolha seu perfil (ou clique nos botões do controle para personalizar), clique em **▶ Salvar e Iniciar Controle** e clique na janela do **Chiaki-ng** / **PS Remote Play** / **PXPlay**.
5. Confira **Semáforo & Diag.** para garantir 60 FPS, VSync desligado e Keymap limpo no Chiaki-ng.

### Atalhos Padrão

| Tecla | Função |
|---|---|
| `F` | Entrar/Sair do veículo (`△` + alterna A Pé / Veículo) |
| `F1` | Trocar A Pé / Veículo manualmente (silencioso) |
| `F2` | Ativar / Desativar **MODO VOO** (avião / helicóptero) |
| `F7` / `F8` | Diminuir / Aumentar sensibilidade da câmera ao vivo (`-10%` / `+10%`) |
| `F12` | Pausar / Reativar a captura de teclado e mouse |
| `Alt+Tab` / `Alt+F4` | Sempre livres para saída de emergência |

---

## Compilação Reprodutível a partir do Código-Fonte

```powershell
# 1. Criar ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools

# 2. Instalar dependências (impedindo o instalador MSI antigo embutido no vgamepad)
$env:VGAMEPAD_SKIP_VIGEMBUS_INSTALL = "true"
pip install -r requirements.txt
pip install pyinstaller

# 3. Rodar bateria completa de autotestes
python gta_controle.py --self-test
python editor_controle.py --self-test
python diagnostico.py --self-test

# 4. Compilar release standalone com ícone e SHA-256
.\build_release.ps1
```

## Licença

Distribuído sob a licença **MIT** — veja [LICENSE](LICENSE).
