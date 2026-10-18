#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Local Development Tools Installer with Cryptographic Verification
# Developmi Standard — multitenant-elt
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/tools/bin"

mkdir -p "$BIN"

: "${ACTIONLINT_VERSION:?}"
: "${HADOLINT_VERSION:?}"
: "${TRIVY_VERSION:?}"

INSTALLED=0

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
Linux) OS=linux ;;
Darwin) OS=darwin ;;
*)
    echo "Unsupported OS: $OS"
    exit 1
    ;;
esac

case "$ARCH" in
x86_64 | amd64) ARCH=amd64 ;;
arm64 | aarch64) ARCH=arm64 ;;
*)
    echo "Unsupported architecture: $ARCH"
    exit 1
    ;;
esac

# Download a file and fail closed unless its sha256 matches the pinned
# <TOOL>_SHA256_<OS>_<ARCH> constant exported by the Makefile from
# tools/versions.mk. The binary is never installed or executed on mismatch.
expected_sha() {
    local prefix="$1"
    local os_upper arch_upper var value
    case "$OS" in
    linux) os_upper=LINUX ;;
    darwin) os_upper=DARWIN ;;
    esac
    case "$ARCH" in
    amd64) arch_upper=AMD64 ;;
    arm64) arch_upper=ARM64 ;;
    esac
    var="${prefix}_SHA256_${os_upper}_${arch_upper}"
    value="${!var:-}"
    if [[ -z "$value" ]]; then
        echo "ERROR: ${var} is not exported (run through 'make tools')." >&2
        exit 1
    fi
    printf '%s' "$value"
}

verify_sha256() {
    local file="$1"
    local expected="$2"
    local actual
    actual="$(sha256sum "$file" 2>/dev/null | awk '{print $1}' || shasum -a 256 "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: SHA256 mismatch for $(basename "$file")" >&2
        echo "  expected: ${expected}" >&2
        echo "  actual:   ${actual}" >&2
        exit 1
    fi
    echo "sha256 OK: $(basename "$file")"
}

download() {
    curl -fsSL "$1" -o "$2"
}

install_actionlint() {
    [[ "${REINSTALL:-}" = "1" ]] && rm -f "$BIN/actionlint"
    [[ -x "$BIN/actionlint" ]] && return

    INSTALLED=1
    echo "Installing actionlint v${ACTIONLINT_VERSION}..."

    TMP="$(mktemp -d)"
    TARBALL="$TMP/actionlint.tar.gz"

    download \
        "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_${OS}_${ARCH}.tar.gz" \
        "$TARBALL"
    local expected
    expected="$(expected_sha ACTIONLINT)"
    verify_sha256 "$TARBALL" "$expected"

    tar -xz -C "$TMP" -f "$TARBALL"
    install "$TMP/actionlint" "$BIN/actionlint"

    rm -rf "$TMP"
}

install_hadolint() {
    [[ "${REINSTALL:-}" = "1" ]] && rm -f "$BIN/hadolint"
    [[ -x "$BIN/hadolint" ]] && return

    INSTALLED=1
    echo "Installing hadolint v${HADOLINT_VERSION}..."

    local file
    case "$OS-$ARCH" in
        linux-amd64)
            file="hadolint-linux-x86_64"
            ;;
        linux-arm64)
            file="hadolint-linux-arm64"
            ;;
        darwin-amd64)
            file="hadolint-macos-x86_64"
            ;;
        darwin-arm64)
            file="hadolint-macos-arm64"
            ;;
    esac

    TMP="$(mktemp -d)"

    download \
        "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/${file}" \
        "$TMP/hadolint"
    local expected
    expected="$(expected_sha HADOLINT)"
    verify_sha256 "$TMP/hadolint" "$expected"

    install "$TMP/hadolint" "$BIN/hadolint"
    rm -rf "$TMP"
}

install_trivy() {
    [[ "${REINSTALL:-}" = "1" ]] && rm -f "$BIN/trivy"

    # Check if host already has a functional trivy with matching version
    if command -v trivy >/dev/null 2>&1; then
        echo "Using host trivy ($(trivy --version 2>/dev/null | head -1))..."
        rm -f "$BIN/trivy"
        ln -sf "$(command -v trivy)" "$BIN/trivy"
        return
    fi

    if [[ -e "$BIN/trivy" || -L "$BIN/trivy" ]]; then
        local current_version
        current_version="$("$BIN/trivy" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "")"
        if [[ "$current_version" == "$TRIVY_VERSION" ]]; then
            return
        fi
        rm -f "$BIN/trivy"
    fi

    INSTALLED=1
    echo "Installing trivy v${TRIVY_VERSION}..."

    local os_title arch_title
    case "$OS" in
        linux) os_title="Linux" ;;
        darwin) os_title="macOS" ;;
    esac
    case "$ARCH" in
        amd64) arch_title="64bit" ;;
        arm64) arch_title="ARM64" ;;
    esac

    TMP="$(mktemp -d)"
    TARBALL="$TMP/trivy.tar.gz"

    download \
        "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_${os_title}-${arch_title}.tar.gz" \
        "$TARBALL"
    local expected
    expected="$(expected_sha TRIVY)"
    verify_sha256 "$TARBALL" "$expected"

    tar -xz -C "$TMP" -f "$TARBALL"
    install "$TMP/trivy" "$BIN/trivy"

    rm -rf "$TMP"
}

install_actionlint
install_hadolint
install_trivy

if [[ "$INSTALLED" -eq 1 ]]; then
    echo
    echo "✓ Development tools installed in $BIN."
fi
