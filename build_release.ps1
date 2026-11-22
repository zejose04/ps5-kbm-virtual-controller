# Script reproduzível de Release v1.0.1 para a comunidade
# Gera pacote isolado em: dist_v1.0\ (NUNCA altera dist\GTA_PS5_Controle_v2.exe)

param(
    [string]$Version = "v1.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
$pyinstaller = ".\.venv\Scripts\pyinstaller.exe"
$vigemDll = (Resolve-Path ".\.venv\Lib\site-packages\vgamepad\win\vigem\client\x64\ViGEmClient.dll").Path
$icoPath = Join-Path $PSScriptRoot "assets\controle_ps5.ico"

if (-not (Test-Path $pyinstaller)) {
    throw "PyInstaller não encontrado em $pyinstaller."
}

# 1. Gera ícone multi-resolução caso ainda não exista
if (-not (Test-Path $icoPath)) {
    Write-Host "Gerando ícone assets\controle_ps5.ico..." -ForegroundColor Cyan
    & $py ".\tools\generate_icon.py"
}

# 2. Executa autotestes antes de compilar
Write-Host "Executando autotestes pré-release..." -ForegroundColor Cyan
& $py gta_controle.py --self-test
if ($LASTEXITCODE -ne 0) { throw "Falha em gta_controle.py --self-test" }
& $py editor_controle.py --self-test
if ($LASTEXITCODE -ne 0) { throw "Falha em editor_controle.py --self-test" }
& $py diagnostico.py --self-test
if ($LASTEXITCODE -ne 0) { throw "Falha em diagnostico.py --self-test" }

# 3. Compila executável standalone isolado em dist_v1.0\
$exeName = "GTA_PS5_Controle_$Version"
$distDir = Join-Path $PSScriptRoot "dist_v1.0"
$buildDir = Join-Path $PSScriptRoot "build_$Version"
New-Item -ItemType Directory -Force -Path $distDir | Out-Null

Write-Host "Compilando $exeName.exe..." -ForegroundColor Cyan
& $pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --distpath "$distDir" `
    --workpath "$buildDir" `
    --specpath "$buildDir" `
    --name "$exeName" `
    --icon "$icoPath" `
    --add-binary "$vigemDll;vgamepad\win\vigem\client\x64" `
    --add-data "$icoPath;assets" `
    --hidden-import "vgamepad" `
    --hidden-import "vgamepad.api" `
    --hidden-import "vgamepad.win" `
    --hidden-import "vgamepad.win.virtual_gamepad" `
    --hidden-import "vgamepad.win.vigem_client" `
    --hidden-import "main" `
    --hidden-import "diagnostico" `
    --hidden-import "gta_controle" `
    --hidden-import "gamepad" `
    --hidden-import "config" `
    editor_controle.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou com código $LASTEXITCODE"
}

# 4. Copia arquivos complementares (preserva mapeamento.json se já existir customizado)
if (-not (Test-Path (Join-Path $distDir "mapeamento.json"))) {
    Copy-Item -Force ".\mapeamento.json" (Join-Path $distDir "mapeamento.json")
}
Copy-Item -Force ".\LICENSE" (Join-Path $distDir "LICENSE")
Copy-Item -Force ".\CHANGELOG.md" (Join-Path $distDir "CHANGELOG.md")

$exe = Join-Path $distDir "$exeName.exe"
$hash = (Get-FileHash -Algorithm SHA256 -Path $exe).Hash
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 2)
"$hash  $exeName.exe" | Set-Content -Encoding UTF8 (Join-Path $distDir "SHA256SUMS.txt")

Write-Host ""
Write-Host "=== RELEASE $Version COMPILADA COM SUCESSO ===" -ForegroundColor Green
Write-Host "Pasta      : $distDir"
Write-Host "Executável : $exe ($sizeMb MB)"
Write-Host "SHA-256    : $hash"
