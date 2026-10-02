"""
Serviço de composição (o "acabamento" da peça).

Sobrepõe na imagem já ambientada:
- um selo de avaliação ("4.8 ★ 3,4mil avaliações") numa caixa arredondada
  translúcida, na parte de baixo;
- uma marca d'água discreta com o @ do canal.

Tudo com Pillow: local, gratuito e determinístico. Ao contrário da IA, aqui
o mesmo input gera sempre a mesma saída, o que facilita testar e ajustar.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class CompositionError(Exception):
    """Exceção específica do serviço de composição, para tratamento isolado no main."""


class CompositionService:
    # Fontes tentadas em ordem. Segoe UI é a do Windows; as outras são
    # alternativas para o robô não quebrar se rodar em outra máquina.
    _FONTES_NEGRITO = (
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "DejaVuSans-Bold.ttf",
    )
    _FONTES_REGULAR = (
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans.ttf",
    )

    _COR_ESTRELA = (255, 193, 7, 255)       # amarelo-ouro
    _COR_TEXTO = (255, 255, 255, 255)
    _COR_CAIXA = (0, 0, 0, 150)             # preto ~60% opaco
    _COR_MARCA_DAGUA = (255, 255, 255, 140) # branco discreto

    def __init__(self, arroba_canal: str | None = None) -> None:
        """
        `arroba_canal` é opcional: sem ele, a peça sai só com o selo.
        O "@" é adicionado automaticamente se faltar.
        """
        if arroba_canal:
            arroba_canal = arroba_canal.strip()
            if not arroba_canal.startswith("@"):
                arroba_canal = f"@{arroba_canal}"
        self._arroba_canal = arroba_canal or None

    # ------------------------------------------------------------------ #
    # Utilitários
    # ------------------------------------------------------------------ #

    def _carregar_fonte(self, tamanho: int, negrito: bool) -> ImageFont.FreeTypeFont:
        caminhos = self._FONTES_NEGRITO if negrito else self._FONTES_REGULAR
        for caminho in caminhos:
            try:
                return ImageFont.truetype(caminho, tamanho)
            except OSError:
                continue
        # Último recurso: fonte embutida do Pillow (feia, mas não quebra).
        return ImageFont.load_default(size=tamanho)

    @staticmethod
    def formatar_quantidade(quantidade: int) -> str:
        """
        Abrevia como os canais de referência fazem:
        151 -> '151', 3423 -> '3,4mil', 1250000 -> '1,2mi'.
        """
        if quantidade < 0:
            raise CompositionError(f"Quantidade de avaliações negativa: {quantidade}")
        if quantidade >= 1_000_000:
            valor, sufixo = quantidade / 1_000_000, "mi"
        elif quantidade >= 1_000:
            valor, sufixo = quantidade / 1_000, "mil"
        else:
            return str(quantidade)
        # Trunca (não arredonda) para nunca exagerar o número real.
        valor = math.floor(valor * 10) / 10
        texto = f"{valor:.1f}".replace(".", ",").replace(",0", "")
        return f"{texto}{sufixo}"

    @staticmethod
    def _desenhar_estrela(
        desenho: ImageDraw.ImageDraw, centro: tuple[float, float], raio: float, cor: tuple
    ) -> None:
        """
        Desenha a estrela como polígono em vez de usar o emoji ⭐.
        Emoji depende de fonte colorida instalada; o polígono funciona sempre.
        """
        cx, cy = centro
        pontos = []
        for i in range(10):
            angulo = math.pi / 2 + i * math.pi / 5
            r = raio if i % 2 == 0 else raio * 0.45
            pontos.append((cx + r * math.cos(angulo), cy - r * math.sin(angulo)))
        desenho.polygon(pontos, fill=cor)

    # ------------------------------------------------------------------ #
    # Elementos da peça
    # ------------------------------------------------------------------ #

    def _aplicar_selo(self, camada: Image.Image, nota: float, quantidade: int) -> None:
        """Desenha o selo de avaliação centralizado na parte de baixo."""
        largura, altura = camada.size
        desenho = ImageDraw.Draw(camada)

        tamanho_fonte = round(largura * 0.042)
        fonte_nota = self._carregar_fonte(tamanho_fonte, negrito=True)
        fonte_texto = self._carregar_fonte(tamanho_fonte, negrito=False)

        texto_nota = f"{nota:.1f}"
        texto_resto = f"{self.formatar_quantidade(quantidade)} avaliações"

        # Mede cada pedaço para montar a caixa no tamanho exato do conteúdo.
        espaco = round(tamanho_fonte * 0.35)
        raio_estrela = tamanho_fonte * 0.5
        largura_nota = desenho.textlength(texto_nota, font=fonte_nota)
        largura_resto = desenho.textlength(texto_resto, font=fonte_texto)
        largura_conteudo = (
            largura_nota + espaco + 2 * raio_estrela + espaco + largura_resto
        )

        margem_x = round(tamanho_fonte * 0.8)
        margem_y = round(tamanho_fonte * 0.45)
        caixa_largura = largura_conteudo + 2 * margem_x
        caixa_altura = tamanho_fonte + 2 * margem_y
        x0 = (largura - caixa_largura) / 2
        y0 = altura * 0.94 - caixa_altura
        desenho.rounded_rectangle(
            (x0, y0, x0 + caixa_largura, y0 + caixa_altura),
            radius=caixa_altura / 2,
            fill=self._COR_CAIXA,
        )

        # anchor="lm": posiciona pelo meio vertical do texto, alinhando
        # nota, estrela e texto na mesma linha sem cálculo manual de baseline.
        centro_y = y0 + caixa_altura / 2
        x = x0 + margem_x
        desenho.text((x, centro_y), texto_nota, font=fonte_nota, fill=self._COR_TEXTO, anchor="lm")
        x += largura_nota + espaco
        self._desenhar_estrela(desenho, (x + raio_estrela, centro_y), raio_estrela, self._COR_ESTRELA)
        x += 2 * raio_estrela + espaco
        desenho.text((x, centro_y), texto_resto, font=fonte_texto, fill=self._COR_TEXTO, anchor="lm")

    def _aplicar_marca_dagua(self, camada: Image.Image) -> None:
        """Escreve o @ do canal no canto superior direito, sem roubar a cena."""
        largura, _ = camada.size
        desenho = ImageDraw.Draw(camada)
        fonte = self._carregar_fonte(round(largura * 0.03), negrito=True)
        margem = round(largura * 0.035)
        desenho.text(
            (largura - margem, margem),
            self._arroba_canal,
            font=fonte,
            fill=self._COR_MARCA_DAGUA,
            anchor="ra",  # ancorado pela direita/topo: não vaza da borda
        )

    # ------------------------------------------------------------------ #
    # Uso principal
    # ------------------------------------------------------------------ #

    def compor(
        self, imagem: Image.Image, avaliacao: tuple[float, int] | None = None
    ) -> Image.Image:
        """
        Devolve uma CÓPIA da imagem com selo e marca d'água aplicados.

        Desenhamos numa camada transparente separada e só no final juntamos
        com alpha_composite: é o que faz a translucidez da caixa funcionar
        (desenhar direto na foto ignoraria o canal alfa da cor).
        """
        try:
            base = imagem.convert("RGBA")
            camada = Image.new("RGBA", base.size, (0, 0, 0, 0))
            if avaliacao is not None:
                self._aplicar_selo(camada, *avaliacao)
            if self._arroba_canal:
                self._aplicar_marca_dagua(camada)
            return Image.alpha_composite(base, camada)
        except (OSError, ValueError) as erro:
            raise CompositionError(f"Falha ao compor a peça: {erro}") from erro


# Bloco de teste rápido.
if __name__ == "__main__":
    caminho = input("Caminho de uma imagem ambientada (ex: output/X_final.png): ").strip()
    try:
        servico = CompositionService(arroba_canal="@achadinhosdodanilo")
        resultado = servico.compor(Image.open(caminho), avaliacao=(4.8, 6345))
        destino = Path(caminho).with_name(Path(caminho).stem + "_composta.png")
        resultado.save(destino)
        print(f"✓ Peça composta salva em: {destino}")
    except FileNotFoundError:
        print(f"[ERRO] Arquivo não encontrado: {caminho}")
    except CompositionError as erro:
        print(f"[ERRO] {erro}")
