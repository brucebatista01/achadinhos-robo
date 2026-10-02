# Manual rápido do Robô de Achadinhos

## O que o sistema faz hoje

Você entrega um link de produto da **Amazon** com o preço. O robô devolve
uma **peça pronta para divulgar**:

1. **Pega a foto oficial** do produto na Amazon, em alta resolução, junto com
   a nota e o número de avaliações.
2. **Recorta o fundo** da foto (no próprio computador, sem custo).
3. **Uma IA olha o produto**, descobre o que ele é e escolhe um cenário que
   combine (ex.: lanterna numa garagem, creme num banheiro).
4. **Monta a cena:** coloca o produto nesse cenário com aparência de foto
   profissional.
5. **Dá o acabamento:** põe o selo "4.7 ★ 3,5mil avaliações" e o @ do canal.
6. **Escreve o texto da oferta:** uma frase de impacto, o nome, o preço
   DE/POR, o cupom e o link de afiliado.

O resultado fica na pasta `output/`. O robô **não posta** no WhatsApp: quem
revisa e publica é você.

## Como usar (depois de instalado)

1. Abra o arquivo `produtos.txt` e cole um produto por linha:
   ```
   https://amzn.to/abc123 | 129,90 | 199,90 | CUPOM10
   ```
   Os campos são link, preço POR, preço DE e cupom. Os dois últimos são
   opcionais.
2. Abra o PowerShell na pasta do projeto e rode:
   ```
   .\.venv\Scripts\Activate.ps1
   python main.py
   ```
3. Espere: cada produto leva cerca de 1 minuto. Se um der erro, o robô pula
   para o próximo e avisa no final.
4. Abra a pasta `output/`. Para cada produto há:
   - `CODIGO_final.png`: a imagem pronta.
   - `CODIGO_mensagem.txt`: o texto pronto para copiar e colar.
5. **Revise antes de publicar.** A imagem e a frase são feitas por IA e às
   vezes saem estranhas. Se não gostar, rode de novo: sai um resultado novo.

A instalação e a configuração das chaves estão no `README.md`.

## Limites atuais

- Só funciona com **Amazon** (a Shopee bloqueia robôs).
- Com a chave grátis do PhotoRoom, a imagem sai com a marca "Photoroom".
  Tirar essa marca exige a chave paga (US$ 0,10 por imagem).
- Custo da IA de texto e visão: uma fração de centavo por produto.
