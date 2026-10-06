# Envia o código deste PC (último commit) para a VPS e reinstala o robô.
# Não usa o GitHub: o projeto antigo (E:\Danilo) e o repositório dele ficam intocados.

$Vps = "root@179.236.235.95"
$Chave = "$HOME\.ssh\achadinhos_vps"
$Pacote = "$env:TEMP\achadinhos-robo.tar"

git -C $PSScriptRoot archive --format=tar -o $Pacote HEAD
scp -i $Chave $Pacote "${Vps}:/root/achadinhos-robo.tar"
ssh -i $Chave $Vps "mkdir -p /opt/achadinhos-robo && tar -xf /root/achadinhos-robo.tar -C /opt/achadinhos-robo && bash /opt/achadinhos-robo/deploy/instalar_robo.sh"
