param(
    [Parameter(Mandatory = $true)]
    [string]$SourceIcon,

    [Parameter(Mandatory = $true)]
    [string]$DestinationDirectory
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function Read-UInt16([byte[]]$Bytes, [int]$Offset) {
    [BitConverter]::ToUInt16($Bytes, $Offset)
}

function Read-UInt32([byte[]]$Bytes, [int]$Offset) {
    [BitConverter]::ToUInt32($Bytes, $Offset)
}

function New-DibIconFrame([System.Drawing.Image]$Source, [int]$Size) {
    $bitmap = [System.Drawing.Bitmap]::new(
        $Size,
        $Size,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.DrawImage($Source, [System.Drawing.Rectangle]::new(0, 0, $Size, $Size))

        # ICO uses bottom-up BGRA pixel rows followed by a 1-bit transparency mask.
        # This DIB encoding avoids the black-background issue some Explorer versions
        # have when a shortcut points straight at a PNG-compressed ICO.
        $xor = [byte[]]::new($Size * $Size * 4)
        $andStride = [int](([Math]::Floor(($Size + 31) / 32)) * 4)
        $andMask = [byte[]]::new($andStride * $Size)

        for ($row = 0; $row -lt $Size; $row++) {
            $sourceY = $Size - 1 - $row
            for ($x = 0; $x -lt $Size; $x++) {
                $color = $bitmap.GetPixel($x, $sourceY)
                $pixelOffset = (($row * $Size) + $x) * 4
                $xor[$pixelOffset] = $color.B
                $xor[$pixelOffset + 1] = $color.G
                $xor[$pixelOffset + 2] = $color.R
                $xor[$pixelOffset + 3] = $color.A
                if ($color.A -lt 128) {
                    $maskOffset = ($row * $andStride) + [int][Math]::Floor($x / 8)
                    if ($maskOffset -ge $andMask.Length) {
                        throw "Transparency-mask offset $maskOffset is outside $($andMask.Length) bytes (size=$Size, row=$row, x=$x, stride=$andStride)."
                    }
                    $andMask[$maskOffset] = $andMask[$maskOffset] -bor (0x80 -shr ($x % 8))
                }
            }
        }

        $frame = [System.IO.MemoryStream]::new()
        $writer = [System.IO.BinaryWriter]::new($frame)
        try {
            $writer.Write([int]40) # BITMAPINFOHEADER size
            $writer.Write([int]$Size)
            $writer.Write([int]($Size * 2)) # XOR + AND mask heights
            $writer.Write([int16]1)
            $writer.Write([int16]32)
            $writer.Write([int]0)
            $writer.Write([int]$xor.Length)
            $writer.Write([int]0)
            $writer.Write([int]0)
            $writer.Write([int]0)
            $writer.Write([int]0)
            $writer.Write($xor)
            $writer.Write($andMask)
            Write-Output -NoEnumerate $frame.ToArray()
            return
        }
        finally {
            $writer.Dispose()
            $frame.Dispose()
        }
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

$sourcePath = (Resolve-Path -LiteralPath $SourceIcon).Path
$sourceBytes = [System.IO.File]::ReadAllBytes($sourcePath)
if ($sourceBytes.Length -lt 22 -or (Read-UInt16 $sourceBytes 2) -ne 1) {
    throw 'The source file is not a valid ICO file.'
}

$entryCount = Read-UInt16 $sourceBytes 4
$pngSignature = [byte[]](0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
$candidate = $null
for ($index = 0; $index -lt $entryCount; $index++) {
    $entryOffset = 6 + ($index * 16)
    $imageSize = Read-UInt32 $sourceBytes ($entryOffset + 8)
    $imageOffset = Read-UInt32 $sourceBytes ($entryOffset + 12)
    if ($imageOffset + $imageSize -gt $sourceBytes.Length -or $imageSize -lt 8) { continue }
    $isPng = $true
    for ($byteIndex = 0; $byteIndex -lt 8; $byteIndex++) {
        if ($sourceBytes[$imageOffset + $byteIndex] -ne $pngSignature[$byteIndex]) { $isPng = $false; break }
    }
    if (-not $isPng) { continue }
    $width = $sourceBytes[$entryOffset]; if ($width -eq 0) { $width = 256 }
    if ($null -eq $candidate -or $width -gt $candidate.Width) {
        $candidate = [PSCustomObject]@{ Offset = $imageOffset; Size = $imageSize; Width = $width }
    }
}

if ($null -eq $candidate) {
    throw 'The source ICO must include a PNG image frame so its transparent icon can be prepared.'
}

$pngBytes = [byte[]]::new([int]$candidate.Size)
[Array]::Copy($sourceBytes, [int]$candidate.Offset, $pngBytes, 0, [int]$candidate.Size)
$pngStream = [System.IO.MemoryStream]::new($pngBytes, $false)
$sourceImage = [System.Drawing.Image]::FromStream($pngStream)
try {
    $hash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant().Substring(0, 16)
    New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    $outputPath = Join-Path $DestinationDirectory ("etf-monitor-$hash.ico")

    if (-not (Test-Path -LiteralPath $outputPath)) {
        $sizes = 16, 24, 32, 48, 64, 128, 256
        $frames = foreach ($size in $sizes) { New-DibIconFrame -Source $sourceImage -Size $size }
        $stream = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::CreateNew)
        $writer = [System.IO.BinaryWriter]::new($stream)
        try {
            $writer.Write([uint16]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]$frames.Count)
            $dataOffset = 6 + (16 * $frames.Count)
            for ($index = 0; $index -lt $frames.Count; $index++) {
                $size = $sizes[$index]
                $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
                $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
                $writer.Write([byte]0)
                $writer.Write([byte]0)
                $writer.Write([uint16]1)
                $writer.Write([uint16]32)
                $writer.Write([uint32]$frames[$index].Length)
                $writer.Write([uint32]$dataOffset)
                $dataOffset += $frames[$index].Length
            }
            foreach ($frame in $frames) { $writer.Write($frame) }
        }
        finally {
            $writer.Dispose()
            $stream.Dispose()
        }
    }

    Write-Output $outputPath
}
finally {
    $sourceImage.Dispose()
    $pngStream.Dispose()
}
