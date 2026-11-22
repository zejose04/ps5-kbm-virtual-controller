# Changelog

Todas as mudanças relevantes deste projeto estão documentadas abaixo.

## [1.0.1] — Hotfix HUD & Alt+Tab
### Corrigido
- **HUD Mini-Overlay ("tarja preta")**: corrigida a inicialização de `WS_EX_LAYERED` no Windows 11 chamando `SetLayeredWindowAttributes(..., LWA_ALPHA)` após o primeiro ciclo de pintura do Tk + `GetAncestor(..., GA_ROOT)` + `RedrawWindow`, garantindo texto branco nítido, faixa colorida por modo e transparência *click-through* mesmo com o Editor minimizado.
- **Mouse/Teclado após `Alt+Tab`**: removida a correspondência ampla por substring `"remote play"` no título da janela (que dava falso positivo no Explorer/Editor/Navegador abertos na pasta do projeto). Agora o foco valida **exclusivamente pelo nome exato do executável** (`chiaki.exe`, `remoteplay.exe`, `pxplay.exe` etc.) com atualização a 30 ms e `LALT` nunca suprimido no Windows.

## [1.0.0] — Release Comunidade (Fase 1 + Fase 2 + Fase 3)
### Adicionado
- **Semáforo de Latência (somente leitura)**: analisa ViGEmBus, UIPI (Admin), Keymap interno, FPS (60 Hz), VSync, Mouse Touchpad e decodificação por hardware do Chiaki-ng com recomendações práticas.
- **Suporte Multi-Cliente de Remote Play**: detecção automática de foco para **Chiaki-ng**, **PS Remote Play (Sony)** e **PXPlay / PSPlay**.
- **Idioma PT-BR / EN ao vivo**: alternância instantânea no topo da janela (`PT | EN`), incluindo Controle Visual, Sliders, Semáforo e Mini-Overlay.
- **Ícone customizado multi-resolução (`.ico`)** embutido no executável e na barra de tarefas.
- **Gerenciador de Perfis Multi-Jogo + Importar / Exportar `.json`**: perfis integrados para *GTA V / VI*, *Red Dead Redemption 2* e *Shooter / FPS*, além de salvamento personalizado em `perfis\`.
- **MODO VOO / AERONAVE (`F2`)**: 4ª aba no Editor com controles dedicados de avião e helicóptero.
- **Preview ao Vivo no Controle Visual**: botões pressionados, barras analógicas proporcionais de `L2`/`R2` e indicadores dos analógicos `LX/LY` e `RX/RY` em tempo real.
- **Mini-Overlay Flutuante *Click-Through*** sobre o jogo exibindo modo atual, status de mira travada, sensibilidade e avisos rápidos sem roubar foco nem cliques.
- **Ações avançadas**: `CROSS_TAP` (Sprint automático GTA sem cansar o dedo), `L2_TOGGLE` (alternar mira com 1 clique — ótimo para Trackpad), `L2_RAMPA`, `MOD_LENTO` (caminhada 45%) e rampa de direção `LX` no Modo Veículo.
- **Múltiplas teclas por função + Prioridade Modo > Globais**: pílulas interativas `[ Tecla × ]` no Editor, botões `Mouse Meio / Mouse 4 / Mouse 5 / Scroll ↑ / Scroll ↓` e teclas ABNT2 / Numpad (`Alt Esq.`, `Ç`, `Num0–Num9`, etc.).
- **Hotkeys ao vivo de sensibilidade**: `F7` (-10%) e `F8` (+10%) sem sair do jogo.
- **Presets de câmera em 1 clique**: *Mouse Gamer*, *Mouse 125Hz* e *Trackpad*.
- **Empacotamento CI/CD**: `build_release.ps1` com `SHA256SUMS.txt` e workflow `.github/workflows/build-windows.yml`.

## [0.2.0] — Baixa Latência & Trackpad Híbrido (`v2`)
- Loop síncrono único a 200 Hz com timer de 1 ms do Windows (`timeBeginPeriod(1)`) e envio direto `push_state_now()`, eliminando double-buffering.
- Captura de Trackpad não-destrutiva (`CursorCenterTracker` com caixa livre de 160 px) preservando a acumulação sub-pixel do Precision Touchpad.
- Mutex de instância única no Windows para impedir dois controles disputando o teclado/trackpad.
- Detecção somente leitura de conflito UIPI (`RUNASADMIN`) e Keymap interno do Chiaki-ng.

## [0.1.0] — Base Validada & Editor Visual (`v1`)
- Emulação nativa DualShock 4 (`VID_054C / PID_05C4`) via `ViGEmBus 1.22.0` + `vgamepad 0.2.0`, validada ponta a ponta na `SDL3.dll` real do Chiaki-ng e ao vivo no PS5.
- Máquina de estados exclusiva (`MODO A PÉ` / `MODO VEÍCULO`), rampa de `R2` (~100 ms), Touchpad `V`/`M` (500 ms), `F`/`F1`/`F12`.
- Editor Visual clicável do controle PlayStation e compilação standalone PyInstaller.
