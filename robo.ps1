# Controle do robô da VPS a partir do PC.
#
#   .\robo.ps1 status       mostra se está ligado e o último post
#   .\robo.ps1 ligar        volta a postar
#   .\robo.ps1 desligar     para de postar
#   .\robo.ps1 lista        envia o lista.txt deste PC para a VPS
#   .\robo.ps1 historico    últimos posts feitos
#   .\robo.ps1 log          acompanha o robô ao vivo (Ctrl+C para sair)

param([string]$Comando = "status")

$Vps = "root@179.236.235.95"
$Chave = "$HOME\.ssh\achadinhos_vps"

if ($Comando -eq "lista") {
    scp -i $Chave "$PSScriptRoot\lista.txt" "${Vps}:/opt/achadinhos-robo/dados/lista.txt"
    ssh -i $Chave $Vps "chown 1000:1000 /opt/achadinhos-robo/dados/lista.txt"
    Write-Host "Lista enviada. Vale a partir do próximo post."
} else {
    ssh -i $Chave $Vps "robo $Comando"
}
