"""Aplicação de console interativa para testes do controle virtual PlayStation.

Responsabilidades:
    - Exibir menu interativo para testar individualmente botões, D-Pad,
      analógicos (LX, LY, RX, RY), gatilhos analógicos (L2, R2) e rotina completa.
    - Exibir visualização contínua dos valores enviados ao controle virtual.
    - Operar com backend real (ViGEmBus + VDS4Gamepad) quando o driver estiver
      disponível, ou em modo de simulação explícito quando o driver ainda não
      estiver pronto.
    - Garantir encerramento seguro sob saída normal, exceções ou Ctrl+C / SIGTERM.
"""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import time
from typing import Callable

from config import (
    APP_NAME,
    APP_VERSION,
    AUTO_TEST_BUTTONS,
    AXIS_LABELS,
    AXIS_TEST_SEQUENCE,
    AnalogAxis,
    ControllerConfig,
    DPAD_CARDINAL_DIRECTIONS,
    DPAD_LABELS,
    DPadDirection,
    PS_BUTTON_LABELS,
    PSButton,
    TRIGGER_TEST_SEQUENCE,
    TriggerName,
    VIGEMBUS_RECOMMENDED_VERSION,
    VIGEMBUS_RELEASE_URL,
)
from diagnostico import format_environment_report, run_live_sdl3_validation
from gamepad import (
    BackendDependencyError,
    DriverUnavailableError,
    SimulatedDS4Backend,
    VDS4ViGEmBackend,
    VirtualPlayStationController,
    probe_vigembus_driver,
)


# ---------------------------------------------------------------------------
# Utilitários de console Windows (tecla não bloqueante e visualização contínua)
# ---------------------------------------------------------------------------

def _clear_screen() -> None:
    if sys.stdout.isatty():
        os.system("cls" if platform.system() == "Windows" else "clear")


def _key_pressed() -> bool:
    """Detecta tecla pressionada no console Windows sem bloquear."""
    if platform.system() == "Windows" and sys.stdin.isatty():
        try:
            import msvcrt  # noqa: PLC0415

            return bool(msvcrt.kbhit())
        except Exception:
            return False
    return False


def _consume_pending_keys() -> None:
    if platform.system() == "Windows" and sys.stdin.isatty():
        try:
            import msvcrt  # noqa: PLC0415

            while msvcrt.kbhit():
                msvcrt.getwch()
        except Exception:
            pass


def _render_live_step(
    controller: VirtualPlayStationController,
    banner: str,
    duration_s: float,
) -> None:
    """Mantém o estado atual por `duration_s` atualizando a telemetria continuamente."""
    end_time = time.perf_counter() + max(0.0, duration_s)
    refresh_period = 1.0 / max(1.0, controller.config.monitor_refresh_hz)
    first_draw = True

    while True:
        remaining = end_time - time.perf_counter()
        if sys.stdout.isatty():
            _clear_screen()
            print("=" * 74)
            print(banner)
            print("=" * 74)
            print(controller.format_telemetry())
            print("-" * 74)
            print(f"Tempo restante neste passo: {max(0.0, remaining):.2f} s  (Ctrl+C cancela)")
        elif first_draw:
            print(f"\n--- {banner} ---")
            print(controller.format_telemetry())
        first_draw = False

        if remaining <= 0:
            break
        time.sleep(min(refresh_period, remaining))


def _run_continuous_monitor(controller: VirtualPlayStationController) -> None:
    """Exibe visualização contínua em tempo real dos valores enviados."""
    refresh_period = 1.0 / max(1.0, controller.config.monitor_refresh_hz)
    _consume_pending_keys()

    print("\nIniciando monitor contínuo... Pressione qualquer tecla (ou Ctrl+C) para voltar.")
    try:
        while True:
            if sys.stdout.isatty():
                _clear_screen()
            print("=" * 74)
            print("MONITOR CONTÍNUO DO CONTROLE VIRTUAL")
            print("=" * 74)
            print(controller.format_telemetry())
            print("-" * 74)
            print("Pressione qualquer tecla ou Ctrl+C para retornar ao menu.")

            if _key_pressed():
                _consume_pending_keys()
                break
            if not sys.stdin.isatty():
                # Em execução não interativa, evita loop infinito
                break
            time.sleep(refresh_period)
    except KeyboardInterrupt:
        print("\nMonitor contínuo encerrado pelo usuário.")


def _prompt_float(prompt: str, min_val: float, max_val: float) -> float | None:
    raw = input(prompt).strip().replace(",", ".")
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        print("Valor inválido. Informe um número decimal (ex.: 0.50).")
        return None
    if not (min_val <= val <= max_val):
        print(f"Valor fora do intervalo permitido [{min_val:.2f}, {max_val:.2f}].")
        return None
    return val


# ---------------------------------------------------------------------------
# Ações do menu interativo (Seções 5 e 6)
# ---------------------------------------------------------------------------

def action_test_buttons(controller: VirtualPlayStationController) -> None:
    buttons = list(PSButton)
    while True:
        print("\n--- TESTAR BOTÕES PLAYSTATION ---")
        for idx, btn in enumerate(buttons, start=1):
            print(f"  {idx:2d}. {PS_BUTTON_LABELS[btn]}")
        print("  15. Executar sequência automática de todos os botões (exceto PS)")
        print("   0. Voltar ao menu principal")

        choice = input("\nEscolha uma opção: ").strip()
        if choice == "0":
            controller.reset_all()
            return
        if choice == "15":
            try:
                for btn in AUTO_TEST_BUTTONS:
                    controller.reset_all()
                    controller.press_button(btn)
                    _render_live_step(
                        controller,
                        f"BOTÃO ATIVO: {PS_BUTTON_LABELS[btn]}",
                        controller.config.default_step_hold_s,
                    )
                    controller.release_button(btn)
                    _render_live_step(
                        controller,
                        f"BOTÃO LIBERADO: {PS_BUTTON_LABELS[btn]}",
                        controller.config.default_step_pause_s,
                    )
            except KeyboardInterrupt:
                print("\nSequência interrompida; restaurando estado neutro.")
            finally:
                controller.reset_all()
            continue

        try:
            idx = int(choice)
            if 1 <= idx <= len(buttons):
                btn = buttons[idx - 1]
                hold = _prompt_float(
                    f"Duração em segundos para manter {btn.value} pressionado [Enter = 0.8s]: ",
                    0.1,
                    10.0,
                )
                hold_s = hold if hold is not None else 0.8
                try:
                    controller.reset_all()
                    controller.press_button(btn)
                    _render_live_step(
                        controller,
                        f"TESTE DE BOTÃO: {PS_BUTTON_LABELS[btn]}",
                        hold_s,
                    )
                except KeyboardInterrupt:
                    print("\nTeste de botão interrompido.")
                finally:
                    controller.reset_all()
                continue
        except ValueError:
            pass
        print("Opção inválida.")


def action_test_dpad(controller: VirtualPlayStationController) -> None:
    options = [
        ("1", DPadDirection.UP),
        ("2", DPadDirection.DOWN),
        ("3", DPadDirection.LEFT),
        ("4", DPadDirection.RIGHT),
    ]
    while True:
        print("\n--- TESTAR D-PAD ---")
        for key, direction in options:
            print(f"  {key}. {DPAD_LABELS[direction]}")
        print("  5. Sequência automática nas 4 direções (UP -> RIGHT -> DOWN -> LEFT)")
        print("  0. Voltar ao menu principal")

        choice = input("\nEscolha uma opção: ").strip()
        if choice == "0":
            controller.reset_all()
            return
        if choice == "5":
            try:
                for direction in DPAD_CARDINAL_DIRECTIONS:
                    controller.set_dpad(direction)
                    _render_live_step(
                        controller,
                        f"D-PAD ATIVO: {DPAD_LABELS[direction]}",
                        controller.config.default_step_hold_s,
                    )
                    controller.set_dpad(DPadDirection.NONE)
                    _render_live_step(
                        controller,
                        "D-PAD NEUTRO",
                        controller.config.default_step_pause_s,
                    )
            except KeyboardInterrupt:
                print("\nSequência do D-Pad interrompida.")
            finally:
                controller.reset_all()
            continue

        matched = next((d for k, d in options if k == choice), None)
        if matched is not None:
            try:
                controller.set_dpad(matched)
                _render_live_step(controller, f"TESTE D-PAD: {DPAD_LABELS[matched]}", 0.8)
            except KeyboardInterrupt:
                print("\nTeste interrompido.")
            finally:
                controller.reset_all()
        else:
            print("Opção inválida.")


def _run_axis_sweep(controller: VirtualPlayStationController, axis: AnalogAxis) -> None:
    try:
        controller.reset_all()
        for label, value in AXIS_TEST_SEQUENCE:
            controller.set_axis(axis, value)
            _render_live_step(
                controller,
                f"EIXO {axis.value} — {label}",
                controller.config.default_step_hold_s,
            )
    except KeyboardInterrupt:
        print("\nVarredura do eixo interrompida.")
    finally:
        controller.reset_all()


def action_test_stick(
    controller: VirtualPlayStationController,
    stick_name: str,
    axis_x: AnalogAxis,
    axis_y: AnalogAxis,
) -> None:
    while True:
        print(f"\n--- TESTAR ANALÓGICO {stick_name.upper()} ({axis_x.value} / {axis_y.value}) ---")
        print(f"  1. Varredura completa eixo horizontal ({AXIS_LABELS[axis_x]})")
        print(f"  2. Varredura completa eixo vertical   ({AXIS_LABELS[axis_y]})")
        print(f"  3. Definir valor manual para {axis_x.value} (-1.00 a +1.00)")
        print(f"  4. Definir valor manual para {axis_y.value} (-1.00 a +1.00)")
        print("  5. Retornar eixos ao centro (0.00, 0.00)")
        print("  0. Voltar ao menu principal")

        choice = input("\nEscolha uma opção: ").strip()
        if choice == "0":
            controller.reset_all()
            return
        if choice == "1":
            _run_axis_sweep(controller, axis_x)
        elif choice == "2":
            _run_axis_sweep(controller, axis_y)
        elif choice in ("3", "4"):
            target_axis = axis_x if choice == "3" else axis_y
            val = _prompt_float(
                f"Informe valor para {target_axis.value} entre -1.00 e +1.00: ",
                -1.0,
                1.0,
            )
            if val is not None:
                hold = _prompt_float("Manter por quantos segundos [Enter = 1.0s]: ", 0.1, 15.0)
                hold_s = hold if hold is not None else 1.0
                try:
                    controller.set_axis(target_axis, val)
                    _render_live_step(
                        controller,
                        f"VALOR MANUAL {target_axis.value} = {val:+.2f}",
                        hold_s,
                    )
                except KeyboardInterrupt:
                    print("\nTeste interrompido.")
                finally:
                    controller.reset_all()
        elif choice == "5":
            controller.set_axis(axis_x, 0.0)
            controller.set_axis(axis_y, 0.0)
            print(f"{axis_x.value} e {axis_y.value} centralizados (0.00).")
        else:
            print("Opção inválida.")


def action_test_trigger(
    controller: VirtualPlayStationController,
    trigger: TriggerName,
) -> None:
    while True:
        print(f"\n--- TESTAR GATILHO ANALÓGICO {trigger.value} (0.00 a 1.00) ---")
        print("  1. Teste escalonado (0.00 -> 0.25 -> 0.50 -> 0.75 -> 1.00 -> 0.00)")
        print("  2. Rampa contínua suave (0.00 até 1.00 e retorno a 0.00)")
        print("  3. Definir valor intermediário manual (0.00 a 1.00)")
        print("  4. Zerar gatilho (0.00)")
        print("  0. Voltar ao menu principal")

        choice = input("\nEscolha uma opção: ").strip()
        if choice == "0":
            controller.reset_all()
            return
        if choice == "1":
            try:
                controller.reset_all()
                for label, val in TRIGGER_TEST_SEQUENCE:
                    controller.set_trigger(trigger, val)
                    _render_live_step(
                        controller,
                        f"GATILHO {trigger.value} — {label}",
                        controller.config.default_step_hold_s,
                    )
            except KeyboardInterrupt:
                print("\nTeste escalonado interrompido.")
            finally:
                controller.reset_all()
        elif choice == "2":
            try:
                controller.reset_all()
                steps = [i / 20.0 for i in range(21)] + [i / 20.0 for i in range(19, -1, -1)]
                for val in steps:
                    controller.set_trigger(trigger, val)
                    _render_live_step(
                        controller,
                        f"RAMPA ANALÓGICA {trigger.value}: {val:.2f}",
                        0.08,
                    )
            except KeyboardInterrupt:
                print("\nRampa interrompida.")
            finally:
                controller.reset_all()
        elif choice == "3":
            val = _prompt_float(
                f"Informe valor analógico para {trigger.value} entre 0.00 e 1.00: ",
                0.0,
                1.0,
            )
            if val is not None:
                hold = _prompt_float("Manter por quantos segundos [Enter = 1.2s]: ", 0.1, 15.0)
                hold_s = hold if hold is not None else 1.2
                try:
                    controller.set_trigger(trigger, val)
                    _render_live_step(
                        controller,
                        f"GATILHO {trigger.value} MANUAL = {val:.2f}",
                        hold_s,
                    )
                except KeyboardInterrupt:
                    print("\nTeste interrompido.")
                finally:
                    controller.reset_all()
        elif choice == "4":
            controller.set_trigger(trigger, 0.0)
            print(f"Gatilho {trigger.value} zerado (0.00).")
        else:
            print("Opção inválida.")


def action_test_all(controller: VirtualPlayStationController) -> None:
    print("\nIniciando teste completo de todos os comandos (Ctrl+C cancela a qualquer momento)...")
    try:
        # 1. Botões
        for btn in AUTO_TEST_BUTTONS:
            controller.reset_all()
            controller.press_button(btn)
            _render_live_step(
                controller,
                f"[1/5 BOTÕES] {PS_BUTTON_LABELS[btn]}",
                0.45,
            )
            controller.release_button(btn)
            time.sleep(0.15)

        # 2. D-Pad
        for direction in DPAD_CARDINAL_DIRECTIONS:
            controller.reset_all()
            controller.set_dpad(direction)
            _render_live_step(
                controller,
                f"[2/5 D-PAD] {DPAD_LABELS[direction]}",
                0.45,
            )
        controller.reset_all()

        # 3. Analógico esquerdo (LX e LY)
        for ax in (AnalogAxis.LX, AnalogAxis.LY):
            for label, val in AXIS_TEST_SEQUENCE:
                controller.set_axis(ax, val)
                _render_live_step(
                    controller,
                    f"[3/5 ANALÓGICO ESQUERDO] {ax.value} — {label}",
                    0.35,
                )
            controller.reset_all()

        # 4. Analógico direito (RX e RY)
        for ax in (AnalogAxis.RX, AnalogAxis.RY):
            for label, val in AXIS_TEST_SEQUENCE:
                controller.set_axis(ax, val)
                _render_live_step(
                    controller,
                    f"[4/5 ANALÓGICO DIREITO] {ax.value} — {label}",
                    0.35,
                )
            controller.reset_all()

        # 5. Gatilhos analógicos L2 e R2
        for trig in (TriggerName.L2, TriggerName.R2):
            for label, val in TRIGGER_TEST_SEQUENCE:
                controller.set_trigger(trig, val)
                _render_live_step(
                    controller,
                    f"[5/5 GATILHOS] {trig.value} — {label}",
                    0.35,
                )
            controller.reset_all()

        print("\nTeste completo finalizado com todos os comandos retornados ao neutro.")
    except KeyboardInterrupt:
        print("\nTeste completo interrompido; comandos zerados com segurança.")
    finally:
        controller.reset_all()


def action_inspect_status(controller: VirtualPlayStationController) -> None:
    print()
    print(format_environment_report(active_controller=controller))
    print("\nTELEMETRIA ATUAL:")
    print(controller.format_telemetry())
    print("\nOpções adicionais:")
    print("  1. Abrir monitor contínuo em tempo real")
    print("  2. Executar validação automática ao vivo na SDL3.dll do Chiaki-ng (requer ViGEmBus)")
    print("  Enter. Voltar ao menu principal")

    sub = input("\nEscolha: ").strip()
    if sub == "1":
        _run_continuous_monitor(controller)
    elif sub == "2":
        if controller.is_virtual_hardware:
            print(
                "\nNota: para evitar dois controles DS4 virtuais simultâneos, "
                "execute 'python diagnostico.py --live' em janela dedicada ou "
                "encerre esta sessão primeiro."
            )
        else:
            passed, items = run_live_sdl3_validation()
            for item in items:
                tag = "PASS" if item.passed else "FAIL"
                print(f"[{tag}] {item.name}: {item.details}")
            print(f"Resultado: {'OK' if passed else 'PENDENTE/FALHA'}")


# ---------------------------------------------------------------------------
# Inicialização controlada do backend e loop principal do menu
# ---------------------------------------------------------------------------

def _create_controller(args: argparse.Namespace) -> VirtualPlayStationController | None:
    cfg = ControllerConfig(update_rate_hz=args.hz)

    if args.simulate:
        ctrl = VirtualPlayStationController(backend=SimulatedDS4Backend(), config=cfg)
        ctrl.start()
        return ctrl

    probe = probe_vigembus_driver()
    if probe.ready_for_virtual_device:
        try:
            ctrl = VirtualPlayStationController(backend=VDS4ViGEmBackend(), config=cfg)
            ctrl.start()
            return ctrl
        except (DriverUnavailableError, BackendDependencyError) as exc:
            print(f"\n[AVISO] Não foi possível inicializar backend real:\n{exc}\n")

    # Driver ainda não disponível: nunca falha abruptamente nem tenta instalar nada
    print("\n" + "=" * 74)
    print("BACKEND REAL (ViGEmBus + VDS4Gamepad) INDISPONÍVEL NO MOMENTO")
    print("=" * 74)
    print(f"Motivo: {probe.summary}")
    print(
        f"Driver recomendado: ViGEmBus {VIGEMBUS_RECOMMENDED_VERSION} "
        f"({VIGEMBUS_RELEASE_URL})"
    )
    print("=" * 74)

    if args.real_only:
        print("Modo --real-only especificado. Encerrando sem criar dispositivo.")
        return None

    if not sys.stdin.isatty():
        print("Sessão não interativa; execute com --simulate ou instale o ViGEmBus primeiro.")
        return None

    while True:
        print("\nComo deseja prosseguir?")
        print("  1. Iniciar em MODO SIMULAÇÃO (sem driver — testa menu, loop ~100 Hz e telemetria)")
        print("  2. Exibir diagnóstico completo do ambiente")
        print("  0. Sair (para instalar o ViGEmBus primeiro)")
        opt = input("\nEscolha: ").strip()
        if opt == "1":
            ctrl = VirtualPlayStationController(backend=SimulatedDS4Backend(), config=cfg)
            ctrl.start()
            return ctrl
        if opt == "2":
            print()
            print(format_environment_report())
        elif opt == "0":
            return None
        else:
            print("Opção inválida.")


def _install_signal_handlers(on_shutdown: Callable[[], None]) -> None:
    def _handler(signum: int, _frame: object) -> None:
        print(f"\nSinal de encerramento recebido ({signum}). Liberando controle virtual...")
        on_shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal.default_int_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handler)


def run_interactive_menu(controller: VirtualPlayStationController) -> None:
    while True:
        print("\n" + "=" * 74)
        print(f"{APP_NAME} (v{APP_VERSION})")
        print("=" * 74)
        print(controller.format_telemetry())
        print("-" * 74)
        print("MENU DE TESTES:")
        print("  1. Testar botões")
        print("  2. Testar D-Pad")
        print("  3. Testar analógico esquerdo (LX / LY)")
        print("  4. Testar analógico direito (RX / RY)")
        print("  5. Testar gatilho L2 (analógico 0.00 a 1.00)")
        print("  6. Testar gatilho R2 (analógico 0.00 a 1.00)")
        print("  7. Testar todos os comandos")
        print("  8. Verificar estado do controle virtual / Monitor contínuo")
        print("  9. Encerrar e liberar dispositivo")
        print("-" * 74)

        try:
            choice = input("Selecione uma opção [1-9]: ").strip()
        except EOFError:
            print("\nEntrada encerrada (EOF). Liberando dispositivo...")
            break

        if choice == "1":
            action_test_buttons(controller)
        elif choice == "2":
            action_test_dpad(controller)
        elif choice == "3":
            action_test_stick(controller, "Esquerdo", AnalogAxis.LX, AnalogAxis.LY)
        elif choice == "4":
            action_test_stick(controller, "Direito", AnalogAxis.RX, AnalogAxis.RY)
        elif choice == "5":
            action_test_trigger(controller, TriggerName.L2)
        elif choice == "6":
            action_test_trigger(controller, TriggerName.R2)
        elif choice == "7":
            action_test_all(controller)
        elif choice == "8":
            action_inspect_status(controller)
        elif choice == "9":
            print("\nEncerrando e liberando dispositivo virtual com segurança...")
            break
        else:
            print("Opção inválida. Informe um número entre 1 e 9.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Executa em modo simulação em memória (não exige ViGEmBus instalado).",
    )
    parser.add_argument(
        "--real-only",
        action="store_true",
        help="Exige backend real ViGEmBus + VDS4Gamepad; sai sem oferecer simulação.",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=100.0,
        help="Frequência alvo do loop de atualização em Hz (padrão: 100.0).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    controller: VirtualPlayStationController | None = None

    try:
        controller = _create_controller(args)
        if controller is None:
            return 0

        _install_signal_handlers(controller.close)
        run_interactive_menu(controller)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupção pelo teclado (Ctrl+C). Zerando e desconectando controle...")
        return 0
    except Exception as exc:
        print(f"\n[ERRO] Falha inesperada durante a execução: {exc}", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            controller.close()
            print("Dispositivo liberado com todos os comandos em estado neutro.")


if __name__ == "__main__":
    raise SystemExit(main())
