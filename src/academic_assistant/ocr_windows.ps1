# Local, memory-only Korean OCR. PNG bytes enter stdin; only JSON leaves stdout.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Await-WinRT($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

$stream = $null
$randomStream = $null
$bitmap = $null
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
        [Windows.Globalization.Language]::new('ko'))
    if ($null -eq $engine) { throw 'Unavailable' }

    $stream = New-Object System.IO.MemoryStream
    $inputStream = [Console]::OpenStandardInput()
    $buffer = New-Object byte[] 65536
    while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        if ($stream.Length + $read -gt 12582912) { throw 'Too large' }
        $stream.Write($buffer, 0, $read)
    }
    $stream.Position = 0
    $randomStream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($stream)
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($randomStream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    if ($decoder.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
        $decoder.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'Too large' }
    $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $words = New-Object System.Collections.Generic.List[object]
    foreach ($line in $result.Lines) {
        foreach ($word in $line.Words) {
            if ($words.Count -ge 20000) { throw 'Too many words' }
            $box = $word.BoundingRect
            $words.Add(@{text=$word.Text; x=$box.X; y=$box.Y; width=$box.Width; height=$box.Height})
        }
    }
    @{width=$decoder.PixelWidth; height=$decoder.PixelHeight; words=@($words.ToArray())} |
        ConvertTo-Json -Compress -Depth 5 | ForEach-Object { [Console]::Write($_) }
} catch {
    # Never expose exception text: native errors can contain private input.
    [Console]::Write('{"error":"ocr_unavailable"}')
    exit 1
} finally {
    if ($null -ne $bitmap) { $bitmap.Dispose() }
    if ($null -ne $randomStream) { $randomStream.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
