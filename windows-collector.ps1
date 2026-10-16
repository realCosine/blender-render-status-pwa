param(
  [Parameter(Mandatory=$true)][string]$Endpoint,
  [Parameter(Mandatory=$true)][string]$Token
)

$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os = Get-CimInstance Win32_OperatingSystem
$ramTotal = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
$ramUsed = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 1)
$blender = Get-Process blender -ErrorAction SilentlyContinue | ForEach-Object { @{ pid=$_.Id; cpu=[math]::Round($_.CPU,1); memory_gb=[math]::Round($_.WorkingSet64/1GB,2) } }
$body = @{ hostname=$env:COMPUTERNAME; cpu_percent=[math]::Round($cpu,1); memory_used_gb=$ramUsed; memory_total_gb=$ramTotal; blender=@($blender); sent_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } | ConvertTo-Json -Depth 4
Invoke-RestMethod -Uri $Endpoint -Method Post -Headers @{"X-Status-Token"=$Token} -ContentType "application/json" -Body $body
