# Erzeugt alle Logo- und Icon-PNGs aus den SVG-Mastern in diesem Ordner (Windows, Microsoft Edge headless).
# Aufruf: powershell -File docs\logo\render.ps1
$repo  = Resolve-Path "$PSScriptRoot\..\.."
$src   = $PSScriptRoot
$brand = "$repo\custom_components\rs_db_hygiene\brand"
$app   = "$repo\rs_db_hygiene"
$tmp   = "$env:TEMP\rs_logo_render"
$edge  = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
New-Item -ItemType Directory -Force $brand, $tmp | Out-Null

$jobs = @(
  @{s="icon.svg";      w=256; h=256; o="$brand\icon.png"},
  @{s="icon.svg";      w=512; h=512; o="$brand\icon@2x.png"},
  @{s="logo.svg";      w=320; h=128; o="$brand\logo.png"},
  @{s="logo.svg";      w=640; h=256; o="$brand\logo@2x.png"},
  @{s="dark_logo.svg"; w=320; h=128; o="$brand\dark_logo.png"},
  @{s="dark_logo.svg"; w=640; h=256; o="$brand\dark_logo@2x.png"},
  @{s="icon.svg";      w=128; h=128; o="$app\icon.png"},
  @{s="app_logo.svg";  w=250; h=100; o="$app\logo.png"}
)
foreach ($j in $jobs) {
  $svg  = (Get-Content "$src\$($j.s)" -Raw).Replace("__W__", "$($j.w)").Replace("__H__", "$($j.h)")
  $html = "<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent;overflow:hidden}svg{display:block}</style></head><body>$svg</body></html>"
  $f = "$tmp\page.html"; Set-Content -Path $f -Value $html -Encoding utf8
  $shot = "$tmp\shot.png"; if (Test-Path $shot) { Remove-Item $shot }
  & $edge --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 `
    --default-background-color=00000000 --window-size="$($j.w),$($j.h)" `
    --user-data-dir="$tmp\profile" --screenshot="$shot" "file:///$($f.Replace('\','/'))" 2>$null | Out-Null
  $t = 0; while (-not (Test-Path $shot) -and $t -lt 50) { Start-Sleep -Milliseconds 200; $t++ }
  Copy-Item $shot $j.o -Force
  "$($j.o)  $($j.w)x$($j.h)"
}
