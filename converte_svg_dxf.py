# converte_svg_dxf.py
import ezdxf
import os
import math

try:
    from svgelements import SVG, Path, Move, Line, CubicBezier, QuadraticBezier, Arc, Close
    print("DEBUG: converte_svg_dxf.py - svgelements importado com sucesso.")
except ImportError:
    print("[ERROR] A biblioteca 'svgelements' não está instalada. Adicione 'svgelements==1.9.6' no requirements.txt.")
    raise

print("DEBUG: converte_svg_dxf.py - Módulo de conversão carregado e pronto.")

def amostrar_segmento_curvo(segmento, passos=15):
    """
    Transforma uma curva (Bezier Cúbica, Quadrática ou Arco) em uma lista de pontos (X, Y).
    Isso facilita muito a leitura em máquinas Laser e softwares CAD, evitando problemas com SPLINEs.
    O eixo Y é invertido (y = -y) porque o SVG cresce para baixo e o DXF cresce para cima.
    """
    pontos = []
    for i in range(passos + 1):
        t = i / passos
        pt = segmento.point(t)
        pontos.append((pt.x, -pt.y))  # Inverte o Y do SVG para o formato DXF
    return pontos

def converter_svg_para_dxf(caminho_svg_entrada: str, caminho_dxf_saida: str) -> bool:
    """
    Lê um arquivo SVG, aplica todas as matrizes de transformações aninhadas (scale, translate, matrix),
    amostra as curvas e salva tudo em um arquivo DXF limpo.
    """
    print(f"[INFO] Iniciando conversão de SVG para DXF. Arquivo: {caminho_svg_entrada}")
    try:
        # 1. Faz o parse matemático do SVG
        svg = SVG.parse(caminho_svg_entrada)
        
        # 2. Prepara o documento DXF
        doc = ezdxf.new('R2010')
        doc.header['$INSUNITS'] = 4  # 4 = Milímetros
        msp = doc.modelspace()
        
        entidades_geradas = 0

        # 3. Itera sobre todos os elementos vetoriais do SVG
        for elemento in svg.elements():
            # Ignora tags estruturais que não são geometria
            nome_tipo = type(elemento).__name__
            if nome_tipo in ('SVG', 'Group', 'Defs', 'Style', 'Use', 'Text'):
                continue
            
            try:
                # Transforma qualquer geometria (Rect, Circle, Ellipse, Polygon) em um Path matemático
                caminho = Path(elemento)
                
                # A MÁGICA ACONTECE AQUI: 
                # Multiplicar o caminho pelo seu 'transform' resolve todas as matrizes
                # de translação, escala e rotação, colocando os pontos no espaço global puro.
                caminho *= caminho.transform
                
                polilinha_atual = []
                
                # Itera sobre os segmentos que formam a figura
                for segmento in caminho:
                    
                    if isinstance(segmento, Move):
                        # Se já havia uma linha sendo desenhada, fecha e joga no DXF
                        if polilinha_atual and len(polilinha_atual) > 1:
                            msp.add_lwpolyline(polilinha_atual)
                            entidades_geradas += 1
                        
                        # Inicia uma nova linha no novo ponto (invertendo Y)
                        polilinha_atual = [(segmento.end.x, -segmento.end.y)]
                        
                    elif isinstance(segmento, Line):
                        # Apenas adiciona o próximo ponto
                        polilinha_atual.append((segmento.end.x, -segmento.end.y))
                        
                    elif isinstance(segmento, (CubicBezier, QuadraticBezier, Arc)):
                        # Transforma a curva em micro-retas (amostragem)
                        pontos_curva = amostrar_segmento_curvo(segmento, passos=15)
                        
                        # O ponto[0] da curva é o mesmo que o último ponto da polilinha atual.
                        # Usamos [1:] para não duplicar o vértice, o que causaria artefatos.
                        polilinha_atual.extend(pontos_curva[1:])
                        
                    elif isinstance(segmento, Close):
                        # Fecha a figura ligando o último ponto ao primeiro
                        if polilinha_atual:
                            if polilinha_atual[0] != polilinha_atual[-1]:
                                polilinha_atual.append(polilinha_atual[0])
                            
                            msp.add_lwpolyline(polilinha_atual)
                            entidades_geradas += 1
                            polilinha_atual = [] # Zera para a próxima figura
                
                # Se após o loop de segmentos ainda sobrou uma linha aberta na memória, salva ela
                if polilinha_atual and len(polilinha_atual) > 1:
                    msp.add_lwpolyline(polilinha_atual)
                    entidades_geradas += 1

            except Exception as e_elemento:
                print(f"[WARN] Aviso ao processar um sub-elemento do SVG: {e_elemento}")

        # Salva o arquivo gerado
        doc.saveas(caminho_dxf_saida)
        
        if entidades_geradas == 0:
            print(f"[WARN] Nenhuma entidade desenhável encontrada no SVG '{caminho_svg_entrada}'. O DXF ficará vazio.")
        else:
            print(f"[SUCCESS] Arquivo convertido salvo com {entidades_geradas} polilinhas em: {caminho_dxf_saida}")
            
        return True
        
    except Exception as ex:
        print(f"[ERROR] Falha crítica ao converter SVG '{caminho_svg_entrada}' para DXF: {ex}")
        return False