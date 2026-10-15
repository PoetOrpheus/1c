#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements.sha256"

error() {
    printf '\nОшибка: %s\n' "$*" >&2
    exit 1
}

install_mint_package() {
    local package="$1"
    local purpose="$2"

    printf '\nДля %s нужен системный пакет %s.\n' "$purpose" "$package"
    if [[ ! -t 0 ]]; then
        error "Установите его командой: sudo apt install $package"
    fi

    read -r -p "Установить $package сейчас? [Y/n] " answer
    if [[ -n "$answer" && ! "$answer" =~ ^[YyДд]$ ]]; then
        error "Установите пакет вручную: sudo apt install $package"
    fi
    command -v apt >/dev/null 2>&1 \
        || error "Пакетный менеджер apt не найден. Установите $package вручную."
    command -v sudo >/dev/null 2>&1 \
        || error "Команда sudo не найдена. Установите $package от имени root."
    sudo apt install -y "$package" \
        || error "Не удалось установить $package."
}

command -v "$PYTHON_BIN" >/dev/null 2>&1 \
    || error "Python 3 не найден. Установите его: sudo apt install python3"

if ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
    install_mint_package "python3-tk" "графического интерфейса"
fi
"$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1 \
    || error "Python по-прежнему не видит tkinter. Проверьте установку python3-tk."

if [[ ! -x "$VENV_PYTHON" ]]; then
    printf 'Создаю виртуальное окружение .venv...\n'
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        install_mint_package "python3-venv" "виртуального Python-окружения"
        "$PYTHON_BIN" -m venv "$VENV_DIR" \
            || error "Не удалось создать .venv."
    fi
fi

requirements_hash="$(sha256sum "$REQUIREMENTS" | cut -d ' ' -f 1)"
installed_hash=""
if [[ -f "$REQUIREMENTS_MARKER" ]]; then
    read -r installed_hash < "$REQUIREMENTS_MARKER"
fi

if [[ "$requirements_hash" != "$installed_hash" ]]; then
    printf 'Устанавливаю Python-зависимости...\n'
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS" \
        || error "Не удалось установить зависимости из requirements.txt."
    printf '%s\n' "$requirements_hash" > "$REQUIREMENTS_MARKER"
fi

cd "$SCRIPT_DIR"
printf 'Запускаю приложение...\n'
exec "$VENV_PYTHON" "$SCRIPT_DIR/main.pyw" "$@"
