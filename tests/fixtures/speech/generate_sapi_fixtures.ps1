$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$fixtureDirectory = Join-Path $PSScriptRoot ''
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.Rate = 0
    $synth.Volume = 100

    $synth.SelectVoice('Microsoft Irina Desktop')
    $synth.SetOutputToWaveFile((Join-Path $fixtureDirectory 'ru.wav'))
    $synth.Speak('Добавь напоминание о встрече завтра в десять.')
    $synth.SetOutputToNull()

    $synth.SelectVoice('Microsoft Zira Desktop')
    $synth.SetOutputToWaveFile((Join-Path $fixtureDirectory 'en.wav'))
    $synth.Speak('Add a reminder for the meeting tomorrow at ten.')
    $synth.SetOutputToNull()
}
finally {
    $synth.Dispose()
}

Get-FileHash (Join-Path $fixtureDirectory 'ru.wav') -Algorithm SHA256
Get-FileHash (Join-Path $fixtureDirectory 'en.wav') -Algorithm SHA256
