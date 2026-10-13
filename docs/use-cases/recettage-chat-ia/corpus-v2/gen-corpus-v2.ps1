param([string]$Out)
# Corpus v2 — partie Office / images. Lit les .src.txt produits par gen-corpus-v2.py.
$ErrorActionPreference = 'Stop'
function Read-Src([string]$name) { Get-Content -Path (Join-Path $Out $name) -Raw -Encoding UTF8 }

function New-WordDoc([string]$text, [string]$docx, [string]$pdf) {
  $w = New-Object -ComObject Word.Application; $w.Visible = $false
  try {
    $d = $w.Documents.Add()
    $d.Content.Text = $text
    $d.Content.Font.Name = 'Calibri'; $d.Content.Font.Size = 11
    $p = $d.Paragraphs.Item(1).Range; $p.Font.Bold = $true; $p.Font.Size = 14
    if ($docx) { $d.SaveAs2($docx, 16) }
    if ($pdf) { $d.ExportAsFixedFormat($pdf, 17) }
    $pages = $d.ComputeStatistics(2)
    $d.Close(0)
    return $pages
  } finally { $w.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($w) | Out-Null }
}

Add-Type -AssemblyName System.Drawing
function New-TextImage([string]$text, [string]$path, [int]$w, [int]$h, [single]$angle, [string]$font, [int]$size) {
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.Clear([System.Drawing.Color]::FromArgb(252, 250, 245))
  $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAlias
  $g.TranslateTransform($w / 2, $h / 2); $g.RotateTransform($angle); $g.TranslateTransform(-$w / 2, -$h / 2)
  $f = New-Object System.Drawing.Font($font, $size)
  $rect = New-Object System.Drawing.RectangleF(80, 80, ($w - 160), ($h - 160))
  $g.DrawString($text, $f, [System.Drawing.Brushes]::Black, $rect)
  $rnd = New-Object System.Random 7
  for ($i = 0; $i -lt ($w * $h / 600); $i++) { $bmp.SetPixel($rnd.Next(0, $w), $rnd.Next(0, $h), [System.Drawing.Color]::FromArgb(140, 140, 140)) }
  $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
}

$log = @()
# L4 attestation (DOCX 1 page)
$log += "L4 pages=" + (New-WordDoc (Read-Src 'L4_attestation_employeur_benali.src.txt') "$Out\L4_attestation_employeur_benali.docx" $null)
# L5 carte de visite (PNG)
New-TextImage (Read-Src 'L5_carte_visite.src.txt') "$Out\L5_carte_visite.png" 900 520 0 'Arial' 22
$log += "L5 ok"
# V1 bail commercial (PDF long)
$log += "V1 pages=" + (New-WordDoc (Read-Src 'V1_bail_commercial_fournil.src.txt') $null "$Out\V1_bail_commercial_fournil.pdf")
# V2 dossier du personnel (DOCX long)
$log += "V2 pages=" + (New-WordDoc (Read-Src 'V2_dossier_personnel_rhone_gestion.src.txt') "$Out\V2_dossier_personnel_rhone_gestion.docx" $null)
# V3 grand livre (XLSX depuis fichier tabulé)
$x = New-Object -ComObject Excel.Application; $x.Visible = $false; $x.DisplayAlerts = $false
try {
  $x.Workbooks.OpenText("$Out\V3_grand_livre_2026.tab.txt", 65001, 1, 1, 1, $false, $true)
  $wb = $x.ActiveWorkbook; $ws = $wb.Worksheets.Item(1); $ws.Name = 'Grand livre 2026'
  $ws.Range('A1:F1').Font.Bold = $true
  $ws.Range('A5003').Value2 = 'TOTAL'; $ws.Range('E5003').Formula = '=SUM(E2:E5001)'; $ws.Range('F5003').Formula = '=SUM(F2:F5001)'
  $ws.Columns.AutoFit() | Out-Null
  $wb.SaveAs("$Out\V3_grand_livre_2026.xlsx", 51)
  $log += "V3 rows=" + $ws.UsedRange.Rows.Count + " sumE=" + $ws.Range('E5003').Value2 + " sumF=" + $ws.Range('F5003').Value2
  $wb.Close($false)
} finally { $x.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($x) | Out-Null }
# V5 courriers scannés (3 images -> PDF sans couche texte)
$imgs = @()
for ($i = 1; $i -le 3; $i++) {
  $p = "$env:TEMP\vk_scan_$i.png"
  $font = @('Segoe Script', 'Calibri', 'Arial')[$i - 1]
  New-TextImage (Read-Src "V5_courrier_$i.src.txt") $p 1240 1754 (0.7 * (($i % 2) * 2 - 1)) $font 24
  $imgs += $p
}
$w = New-Object -ComObject Word.Application; $w.Visible = $false
try {
  $d = $w.Documents.Add()
  $d.PageSetup.TopMargin = 20; $d.PageSetup.BottomMargin = 20; $d.PageSetup.LeftMargin = 20; $d.PageSetup.RightMargin = 20
  $r = $d.Content
  foreach ($p in $imgs) {
    $shape = $d.InlineShapes.AddPicture($p, $false, $true, $r)
    $shape.LockAspectRatio = -1; $shape.Width = 540
    $r = $d.Content; $r.Collapse(0)
    if ($p -ne $imgs[-1]) { $r.InsertBreak(7); $r = $d.Content; $r.Collapse(0) }
  }
  $d.ExportAsFixedFormat("$Out\V5_courriers_scannes_3p.pdf", 17)
  $log += "V5 pages=" + $d.ComputeStatistics(2)
  $d.Close(0)
} finally { $w.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($w) | Out-Null }
$imgs | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }
$log -join "`n"
Get-ChildItem $Out -Exclude *.src.txt, *.tab.txt | Select-Object Name, Length | Format-Table -AutoSize | Out-String
