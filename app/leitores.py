"""Leitores de formatos de engenharia e identificação de documentos pelo código Klabin.

Cada leitor devolve uma lista de trechos (texto, local, data ISO ou None), no mesmo formato
usado pelo resto da ingestão:

- planilhas (.xlsx/.xlsm/.xls): detecta a linha de cabeçalho (inclusive grupos de colunas
  mesclados) e escreve cada linha como "Coluna: valor; Coluna: valor", para que a busca por uma
  tag traga a linha inteira com significado;
- Visio (.vsdx/.vsdm): texto de cada forma + dados de forma (código de item, fornecedor...), por página;
- AutoCAD (.dwg via LibreDWG `dwg2dxf`, .dxf direto): textos, MTEXT, tabelas e atributos de blocos;
- código Klabin (ex. 34231-I-LIO-00117-0001): área, máquina, disciplina e tipo de documento.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date, datetime, time
from pathlib import Path
from xml.etree import ElementTree as ET

Trecho = tuple[str, str, str | None]

TRECHO_MAX = 1800
MAX_LINHAS_PLANILHA = 50_000
MAX_COLUNAS_PLANILHA = 200
DWG_TIMEOUT_S = 300

# --------------------------------------------------------------------------- código de documento Klabin

RX_CODIGO = re.compile(r"(?<![0-9A-Za-z])(3\d{4})-([A-Z])-([A-Z]{3})-(\d{5})-(\d{4})(?![0-9])")

DISCIPLINAS = {"A": "Automação", "E": "Elétrica", "I": "Instrumentação", "M": "Mecânica", "C": "Civil",
               "P": "Processo", "T": "Tubulação", "G": "Geral", "N": "Planejamento"}

# Siglas da planilha "Tipodoc" do padrão de documentos Klabin (Puma II).
TIPOS_DOC = {
    "ADM": "Documentos administrativos / coordenação", "ALV": "Alvenaria", "AMB": "Gestão ambiental",
    "APR": "Análise técnica de propostas", "ARQ": "Arquitetura", "AVC": "HVAC",
    "BKP": "Backups dos aplicativos de automação", "BOK": "Databook", "CAB": "Lista de cabos (de-para)",
    "CAL": "Memorial de cálculo", "CCA": "Diagramas de CCA (CCM média tensão)",
    "CCM": "Diagramas de CCMi (CCM baixa tensão)", "CEL": "Diagramas de comando elétrico",
    "CLP": "Diagramas de CLPs", "CMT": "Comentários técnicos", "CNG": "Cronogramas",
    "COM": "Mapas de comunicação (SDCD, CCMi, PLC, drives, analisadores)",
    "CRT": "Critério e especificação geral de projeto", "CSS": "Comissionamento",
    "CTR": "Desenhos construtivos de civil", "DCP": "Diagramas de controle e proteção",
    "DEQ": "Diagramas de equipamentos dedicados/especiais", "DHP": "Diagramas hidráulicos e pneumáticos",
    "DIT": "Diagramas de intertravamento/lógico/controle/SAMA", "DPR": "Documentos de processo",
    "DTB": "Detalhes de tubulação", "DTM": "Arquivos DTM", "ELP": "Projeto elétrico predial",
    "ENC": "Diagrama de encaminhamentos", "EPC": "Especificação para compra",
    "EQU": "Dimensionais, especificação e manuais de equipamentos e instrumentos",
    "EST": "Estudos de curto-circuito, seletividade e demanda", "FDD": "Folha de dados e curvas de desempenho",
    "FUN": "Diagramas funcionais", "FXG": "Fluxogramas e P&I", "GER": "Diagramas de geradores",
    "GMO": "Gerenciamento de obras", "GST": "Arquivos GSD", "IEX": "Infraestrutura externa",
    "ILU": "Iluminação e tomadas", "IMT": "Insertos metálicos", "INC": "Combate a incêndio",
    "INT": "Diagramas de interligação", "IPS": "Instalações prediais (infra seca)", "ISO": "Isométricos",
    "ITR": "Instruções de partida, instalação, operação e manutenção", "LDD": "Lista de desenhos/documentos",
    "LEG": "Documentos legais", "LEQ": "Lista de equipamentos, linhas e válvulas", "LIN": "Lista de instrumentos",
    "LIO": "Lista de I/O", "LMO": "Lista de motores", "LPR": "Lista de peças de reposição",
    "MAT": "Lista de materiais", "MET": "Estrutura metálica", "MLH": "Diagrama de malhas",
    "MNT": "Manual técnico (operação e manutenção)", "MOB": "Mobiliário", "MOD": "Modelos 3D",
    "MON": "Desenhos mecânicos de montagem", "NRS": "Normas regulamentadoras",
    "OTS": "Documentos das OTS (simulador de processo/SDCD)", "PEC": "Dimensionais de peças e componentes",
    "PEL": "Plantas de elétrica", "PIN": "Plantas de instrumentação", "PLT": "Plantas e layouts",
    "PNL": "Diagrama de painéis do SDCD", "PQT": "Lista de plaquetas de identificação",
    "PRO": "Estimativa de preços / propostas", "QDA": "Diagramas das alimentações (QDA)",
    "QLD": "Documentos de qualidade", "RED": "Arquitetura das redes de automação", "REF": "Refratários",
    "REL": "Relatórios técnicos", "REM": "Remotas de campo / alocação de I/O",
    "RVE": "Revestimentos específicos", "SBS": "Sobressalentes e consumíveis",
    "SEG": "Documentos de segurança", "SEL": "Layout de salas de controle e elétricas",
    "SFT": "Programas e licenças de software", "SON": "Relatório de sondagens", "SUB": "Layout de subestações",
    "TEL": "Layout das telas gráficas e pop-ups", "TIP": "Típicos de montagem", "TOP": "Topografia",
    "TPR": "Especificação para tomada de preço", "TRI": "Diagramas trifilares",
    "TRN": "Treinamento", "TRS": "Linhas de transmissão 230 kV", "UNI": "Diagramas unifilares",
}

# Áreas da planilha "Areas" do padrão Klabin (só as da MP27 vêm descritas nos documentos recebidos).
AREAS = {
    "34000": "Máquinas de Papel", "34100": "Máquinas de Papel - MP27", "34101": "Chuveiros de emergência",
    "34102": "Sistema de combate a incêndio", "34103": "Ventilação de conforto",
    "34104": "HVAC de salas especiais", "34105": "Sistema de água fresca", "34106": "Utilidades",
    "34108": "Distribuição de média tensão", "34109": "Distribuição de baixa tensão",
    "34110": "Coleta e bombeamento de efluentes", "34111": "Preparo de Massa",
    "34112": "Circuito de Aproximação Cobertura e Base", "34113": "Circuito de refugos e quebras",
    "34114": "Recuperação de fibras e água branca", "34115": "Sistema de vácuo",
    "34116": "Sistema de água de chuveiros", "34117": "Sistema de Vapor e Condensado",
    "34119": "Químicos Parte Úmida", "34120": "Caixa de entrada e mesas planas",
    "34121": "Seção de Prensas e Caixas de Vapor", "34122": "Seção Pré-secagem", "34123": "Sizer",
    "34124": "Seção Pós-secagem", "34125": "Calandra", "34126": "Enroladeira",
    "34128": "Sistemas de lubrificação", "34129": "SDCD, Monitoramento e Controle", "34130": "Rebobinadeira",
    "34131": "Linha de bobinas", "34132": "Sistema de corte e transporte de tubetes",
    "34133": "Embalagem de bobinas", "34134": "Estocagem de bobinas", "34135": "Rebobinadeira de reprocesso",
    "34136": "Acionamentos da Rebobinadeira de reprocesso",
    "34140": "Sistema de segurança de acionamentos - Parada de Emergência",
}


def maquina_da_area(area: str) -> str | None:
    """34100-34199 = MP27, 34200-34299 = MP28 (numeração do projeto Puma II)."""
    if area.startswith("341"):
        return "MP27"
    if area.startswith("342"):
        return "MP28"
    return None


def identificar_documento(nome: str) -> dict | None:
    """Lê o código Klabin no nome do arquivo (ex. '34231-I-LIO-00117-0001_EN.pdf')."""
    m = RX_CODIGO.search(nome or "")
    if not m:
        return None
    area, disc, tipo, numero, folha = m.groups()
    return {
        "codigo": m.group(0), "area": area, "area_nome": AREAS.get(area), "maquina": maquina_da_area(area),
        "disciplina": disc, "disciplina_nome": DISCIPLINAS.get(disc), "tipo_doc": tipo,
        "tipo_doc_nome": TIPOS_DOC.get(tipo), "numero": numero, "folha": folha,
        "idioma": "inglês" if re.search(r"[_\-. ]EN(?:[_\-. ]|$)", Path(nome).stem, re.I) else None,
    }


def rotulo_documento(info: dict) -> str:
    """Prefixo curto colocado em cada trecho, para a busca achar o contexto (máquina, tipo)."""
    partes = [info["codigo"]]
    if info.get("maquina"):
        partes.append(info["maquina"])
    if info.get("tipo_doc_nome"):
        partes.append(info["tipo_doc_nome"])
    return "[" + " · ".join(partes) + "]"


def ficha_documento(info: dict, nome: str) -> str:
    linhas = [f"FICHA DO DOCUMENTO {info['codigo']} (arquivo {nome})"]
    if info.get("maquina"):
        linhas.append(f"Máquina: {info['maquina']}")
    linhas.append(f"Área: {info['area']}" + (f" - {info['area_nome']}" if info.get("area_nome") else ""))
    linhas.append(f"Disciplina: {info['disciplina']}" +
                  (f" - {info['disciplina_nome']}" if info.get("disciplina_nome") else ""))
    linhas.append(f"Tipo de documento: {info['tipo_doc']}" +
                  (f" - {info['tipo_doc_nome']}" if info.get("tipo_doc_nome") else ""))
    linhas.append(f"Número {info['numero']}, folha {info['folha']}")
    if info.get("idioma"):
        linhas.append(f"Idioma: {info['idioma']}")
    return "\n".join(linhas)


def area_documento(info: dict) -> str:
    s = info["area"] + (f" - {info['area_nome']}" if info.get("area_nome") else "")
    return s + (f" ({info['maquina']})" if info.get("maquina") and info["maquina"] not in s else "")


# --------------------------------------------------------------------------- utilidades

def _agrupar(linhas: list[tuple[int, str]], rotulo) -> list[Trecho]:
    """Junta linhas (número, texto) em trechos de até TRECHO_MAX caracteres.

    `rotulo(primeira, ultima)` produz o texto de localização do trecho.
    """
    out: list[Trecho] = []
    bloco: list[str] = []
    ini = fim = None
    tam = 0
    for n, txt in linhas:
        txt = txt[: TRECHO_MAX * 2]
        if bloco and tam + len(txt) + 1 > TRECHO_MAX:
            out.append(("\n".join(bloco), rotulo(ini, fim), None))
            bloco, tam, ini = [], 0, None
        if ini is None:
            ini = n
        fim = n
        bloco.append(txt)
        tam += len(txt) + 1
    if bloco:
        out.append(("\n".join(bloco), rotulo(ini, fim), None))
    return out


def _celula(v) -> str:
    """Valor de célula como texto curto e legível."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "não"
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.6g}"
    s = str(v).replace("\r", "")
    s = re.sub(r"[ \t]*\n[ \t]*", " / ", s.strip())
    return re.sub(r"\s{2,}", " ", s)


# --------------------------------------------------------------------------- planilhas

# Abas de listas auxiliares do modelo de documento Klabin (iguais em todos os arquivos).
ABAS_IGNORADAS = {"status", "areas", "disciplinas", "tipodoc"}
ROTULOS_CAPA = ["Área:", "Disciplina:", "Tipo de Documento:", "Título do documento:", "N° Klabin", "Nº Klabin",
                "N° EPC", "Nº EPC", "Local de Instalação (TAG)", "Status"]


class Aba:
    """Matriz de valores de uma aba + mapa de células mescladas (linha, coluna 0-based)."""

    def __init__(self, nome: str, linhas: list[list], mescladas: dict[tuple[int, int], tuple[int, int]]):
        self.nome = nome
        self.linhas = linhas
        self.mescladas = mescladas  # célula -> célula de origem do grupo mesclado


def _abas_xlsx(path: Path) -> list[Aba]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)  # sem read_only: precisamos das mesclagens
    abas = []
    for ws in wb.worksheets:
        try:  # limites reais (max_column às vezes vem 16384 por formatação na aba inteira)
            chaves = [k for k, c in ws._cells.items() if c.value not in (None, "")]
            max_r = max((r for r, _ in chaves), default=0)
            max_c = max((c for _, c in chaves), default=0)
        except AttributeError:
            max_r, max_c = ws.max_row, ws.max_column
        max_r, max_c = min(max_r, MAX_LINHAS_PLANILHA), min(max_c, MAX_COLUNAS_PLANILHA)
        linhas = [[None] * max_c for _ in range(max_r)]
        for row in ws.iter_rows(min_row=1, max_row=max_r, max_col=max_c):
            for c in row:
                if c.value is not None and hasattr(c, "column"):
                    linhas[c.row - 1][c.column - 1] = c.value
        mescl: dict[tuple[int, int], tuple[int, int]] = {}
        for rg in ws.merged_cells.ranges:
            if rg.min_row > max_r or rg.min_col > max_c:
                continue
            for r in range(rg.min_row, min(rg.max_row, max_r) + 1):
                for c in range(rg.min_col, min(rg.max_col, max_c) + 1):
                    mescl[(r - 1, c - 1)] = (rg.min_row - 1, rg.min_col - 1)
        abas.append(Aba(ws.title, linhas, mescl))
    wb.close()
    return abas


def _abas_xls(path: Path) -> list[Aba]:
    import xlrd

    try:
        wb = xlrd.open_workbook(str(path), formatting_info=True)
    except (NotImplementedError, xlrd.XLRDError):
        wb = xlrd.open_workbook(str(path))
    abas = []
    for sh in wb.sheets():
        max_r, max_c = min(sh.nrows, MAX_LINHAS_PLANILHA), min(sh.ncols, MAX_COLUNAS_PLANILHA)
        linhas = []
        for r in range(max_r):
            linha = []
            for c in range(max_c):
                cel = sh.cell(r, c)
                v = cel.value
                if cel.ctype == xlrd.XL_CELL_DATE:
                    try:
                        v = xlrd.xldate.xldate_as_datetime(v, wb.datemode)
                    except (ValueError, OverflowError):
                        pass
                elif cel.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    v = None
                elif cel.ctype == xlrd.XL_CELL_ERROR:
                    v = None
                linha.append(v)
            linhas.append(linha)
        mescl = {}
        for r0, r1, c0, c1 in getattr(sh, "merged_cells", []) or []:
            for r in range(r0, min(r1, max_r)):
                for c in range(c0, min(c1, max_c)):
                    mescl[(r, c)] = (r0, c0)
        abas.append(Aba(sh.name, linhas, mescl))
    return abas


def ler_abas(path: Path) -> list[Aba]:
    ext = path.suffix.lower()
    if ext == ".xls":
        return _abas_xls(path)
    return _abas_xlsx(path)


def _vazio(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _linha_cabecalho(linhas: list[list]) -> int | None:
    """Escolhe, entre as 40 primeiras linhas, a que mais parece cabeçalho de tabela.

    Critério: mais células de texto (não numéricas) e pelo menos 2 linhas de dados abaixo.
    """
    melhor, melhor_pts = None, 2
    for i, row in enumerate(linhas[:40]):
        textos = [v for v in row if isinstance(v, str) and v.strip() and not re.fullmatch(r"[\d.,\s/-]+", v)]
        pts = len(textos)
        if pts <= melhor_pts:
            continue
        dados = sum(1 for r in linhas[i + 1:i + 30] if sum(1 for v in r if not _vazio(v)) >= 2)
        if dados >= 2:
            melhor, melhor_pts = i, pts
    return melhor


def _nomes_colunas(aba: Aba, h: int) -> list[str]:
    """Nomes das colunas; se a linha acima tem grupos mesclados, vira 'Grupo > Coluna'."""
    linhas = aba.linhas
    ncol = len(linhas[h]) if linhas else 0
    grupos = [""] * ncol
    if h > 0:
        origens = [aba.mescladas.get((h - 1, c)) for c in range(ncol)]
        for c, origem in enumerate(origens):
            if origem and origens.count(origem) > 1:  # célula mesclada cobrindo mais de uma coluna
                grupos[c] = _celula(linhas[origem[0]][origem[1]])
        acima = linhas[h - 1]
        cols = [c for c in range(min(ncol, len(acima))) if not _vazio(acima[c])]
        n_cab = sum(1 for v in linhas[h] if not _vazio(v))
        # Sem mesclagem, mas linha acima esparsa (ex. "From ... To ... Cable"): estende cada grupo à direita.
        if not any(grupos) and len(cols) >= 2 and len(cols) * 2 <= n_cab and \
                any(b - a > 1 for a, b in zip(cols, cols[1:])):
            atual = ""
            for c in range(ncol):
                if c < len(acima) and not _vazio(acima[c]):
                    atual = _celula(acima[c])
                grupos[c] = atual
    nomes = []
    vistos: dict[str, int] = {}
    for c in range(ncol):
        nome = _celula(linhas[h][c]) or f"Coluna {c + 1}"
        if grupos[c] and grupos[c].lower() != nome.lower():
            nome = f"{grupos[c]} > {nome}"
        vistos[nome] = vistos.get(nome, 0) + 1
        if vistos[nome] > 1:
            nome = f"{nome} ({vistos[nome]})"
        nomes.append(nome)
    return nomes


def _capa_klabin(aba: Aba) -> list[str]:
    """Extrai os campos da aba 'Capa' (ignora as listas suspensas que ficam na mesma aba)."""
    out = []
    for row in aba.linhas:
        cels = [(i, v) for i, v in enumerate(row) if not _vazio(v)]
        for k, (i, v) in enumerate(cels):
            s = _celula(v)
            if s in ROTULOS_CAPA and k + 1 < len(cels):
                valor = _celula(cels[k + 1][1])
                if valor and valor not in ROTULOS_CAPA:
                    out.append(f"{s.rstrip(':')}: {valor}")
        textos = [_celula(v) for _, v in cels]
        if textos and textos[0] in ("Rev.",):
            out.append("Revisões: " + " | ".join(textos))
        elif textos and isinstance(cels[0][1], (int, float)) and len(textos) >= 4 and "/" in "".join(textos[1:3]):
            out.append("Revisão " + " | ".join(textos))
    return list(dict.fromkeys(out))


def trechos_aba(aba: Aba) -> list[Trecho]:
    linhas = aba.linhas
    if not any(not _vazio(v) for row in linhas for v in row):
        return []
    nome_aba = aba.nome.strip()
    if nome_aba.lower() == "capa":
        campos = _capa_klabin(aba)
        return [(f"CAPA (aba '{nome_aba}')\n" + "\n".join(campos), f"aba '{nome_aba}'", None)] if campos else []

    h = _linha_cabecalho(linhas)
    saida: list[tuple[int, str]] = []
    if h is None:  # sem tabela reconhecível: linha a linha
        for i, row in enumerate(linhas):
            vals = [_celula(v) for v in row if not _vazio(v)]
            if vals:
                saida.append((i + 1, " | ".join(vals)))
    else:
        topo = [" | ".join(_celula(v) for v in row if not _vazio(v)) for row in linhas[:h]]
        topo = [t for t in topo if t]
        if topo:
            saida.append((1, f"Cabeçalho da aba '{nome_aba}': " + " || ".join(topo)))
        nomes = _nomes_colunas(aba, h)
        for i in range(h + 1, len(linhas)):
            row = linhas[i]
            pares = [(nomes[c] if c < len(nomes) else f"Coluna {c + 1}", _celula(v))
                     for c, v in enumerate(row) if not _vazio(v)]
            if not pares:
                continue
            if len(pares) == 1 and not re.fullmatch(r"[\d.,]+", pares[0][1]):
                saida.append((i + 1, f"## {pares[0][1]}"))  # título de seção dentro da tabela
                continue
            saida.append((i + 1, f"Linha {i + 1}: " + "; ".join(f"{k}: {v}" for k, v in pares)))

    def rotulo(a, b):
        return f"aba '{nome_aba}', linhas {a}-{b}" if a != b else f"aba '{nome_aba}', linha {a}"

    return _agrupar(saida, rotulo)


def trechos_planilha(path: Path) -> list[Trecho]:
    out: list[Trecho] = []
    for aba in ler_abas(path):
        if aba.nome.strip().lower() in ABAS_IGNORADAS:
            continue
        out.extend(trechos_aba(aba))
    return out


def planilha_para_csv(path: Path) -> str:
    """Primeira aba com tabela, a partir do cabeçalho, como CSV (';'). Usado nas importações de alarmes."""
    import csv
    import io

    for aba in ler_abas(path):
        h = _linha_cabecalho(aba.linhas)
        if h is None:
            continue
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        for row in aba.linhas[h:]:
            if any(not _vazio(v) for v in row):
                w.writerow([(v.isoformat(sep=" ") if isinstance(v, datetime) else _celula(v)) for v in row])
        return buf.getvalue()
    raise ValueError("nenhuma tabela encontrada na planilha")


# --------------------------------------------------------------------------- Visio

_NS_VISIO = "{http://schemas.microsoft.com/office/visio/2012/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# Propriedades de formas de cota/linha do Visio que só poluem o texto.
_PROPS_VISIO_IGNORADAS = {"precision", "units", "units display", "extension lines", "angle", "text angle",
                          "dimension", "unidade", "precisão"}


def _texto_xml(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _formas_visio(raiz) -> list[str]:
    linhas = []
    for shape in raiz.iter(f"{_NS_VISIO}Shape"):
        partes = []
        txt = shape.find(f"{_NS_VISIO}Text")
        if txt is not None:
            t = _texto_xml(txt)
            if t:
                partes.append(t)
        dados = []
        for sec in shape.findall(f"{_NS_VISIO}Section"):
            if sec.get("N") != "Property":
                continue
            for row in sec.findall(f"{_NS_VISIO}Row"):
                cel = {c.get("N"): c.get("V") for c in row.findall(f"{_NS_VISIO}Cell")}
                valor = (cel.get("Value") or "").strip()
                if not valor or valor == "No Formula":
                    continue
                rotulo = (cel.get("Label") or row.get("N") or "").strip()
                if rotulo.lower() in _PROPS_VISIO_IGNORADAS:
                    continue
                dados.append(f"{rotulo}: {valor}" if rotulo else valor)
        if dados:
            partes.append("{" + "; ".join(dados) + "}")
        if partes:
            linhas.append(" ".join(partes))
    return list(dict.fromkeys(linhas))  # remove repetidos mantendo a ordem


def trechos_visio(path: Path) -> list[Trecho]:
    out: list[Trecho] = []
    with zipfile.ZipFile(path) as z:
        nomes = set(z.namelist())
        paginas: list[tuple[str, str]] = []
        if "visio/pages/pages.xml" in nomes:
            rels = {}
            if "visio/pages/_rels/pages.xml.rels" in nomes:
                for r in ET.fromstring(z.read("visio/pages/_rels/pages.xml.rels")):
                    rels[r.get("Id")] = r.get("Target")
            for pg in ET.fromstring(z.read("visio/pages/pages.xml")).findall(f"{_NS_VISIO}Page"):
                if pg.get("Background") == "1":
                    continue
                rel = pg.find(f"{_NS_VISIO}Rel")
                alvo = rels.get(rel.get(f"{_NS_REL}id")) if rel is not None else None
                if alvo:
                    paginas.append((pg.get("Name") or pg.get("NameU") or alvo, "visio/pages/" + alvo))
        if not paginas:
            paginas = [(n.rsplit("/", 1)[-1], n) for n in sorted(nomes)
                       if re.fullmatch(r"visio/pages/page\d+\.xml", n)]
        for k, (nome_pag, arq) in enumerate(paginas, start=1):
            if arq not in nomes:
                continue
            linhas = _formas_visio(ET.fromstring(z.read(arq)))
            if not linhas:
                continue
            numeradas = [(1, f"PÁGINA {k}: {nome_pag}")] + [(i + 2, t) for i, t in enumerate(linhas)]
            out.extend(_agrupar(numeradas, lambda a, b, k=k, n=nome_pag: f"página {k} ({n})"))
    return out


# --------------------------------------------------------------------------- AutoCAD (DWG/DXF)

def conversor_dwg() -> str | None:
    return shutil.which("dwg2dxf")


def dwg_para_dxf(path: Path, destino: Path) -> Path:
    exe = conversor_dwg()
    if not exe:
        raise FileNotFoundError("conversor dwg2dxf (LibreDWG) não instalado")
    saida = destino / (path.stem + ".dxf")
    proc = subprocess.run([exe, "-y", "-o", str(saida), str(path)], capture_output=True,
                          timeout=DWG_TIMEOUT_S, cwd=str(destino))
    if not saida.exists() or saida.stat().st_size == 0:
        msg = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["sem saída"]
        raise RuntimeError(f"dwg2dxf falhou (código {proc.returncode}): {msg[0][:200]}")
    return saida


_RX_UNICODE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
_RX_PCT = re.compile(r"%%(\d{3})")


def limpar_texto_cad(s: str, mtext: bool = False) -> str:
    """Remove códigos de formatação do AutoCAD (MTEXT e %%) e decodifica \\U+XXXX."""
    s = _RX_UNICODE.sub(lambda m: chr(int(m.group(1), 16)), s)
    s = _RX_PCT.sub(lambda m: bytes([int(m.group(1)) % 256]).decode("cp1252", "replace"), s)
    s = re.sub(r"%%[cC]", "Ø", s)
    s = re.sub(r"%%[dD]", "°", s)
    s = re.sub(r"%%[pP]", "±", s)
    s = re.sub(r"%%[uUoOkK]", "", s)
    s = s.replace("%%%", "%")
    if mtext:
        s = s.replace("\\\\", "\x00").replace("\\{", "\x01").replace("\\}", "\x02")
        s = s.replace("\\P", "\n")                    # quebra de parágrafo (\p minúsculo é formato)
        s = re.sub(r"\\S([^;]*);", lambda m: re.sub(r"[\^#]", "/", m.group(1)), s)  # empilhado (frações)
        s = re.sub(r"\\[ACcFfHhQTWp][^;\\]*;", "", s)  # fonte, altura, cor, parágrafo...
        s = re.sub(r"\\[LlOoKkNn]", "", s)
        s = s.replace("\\~", " ").replace("{", "").replace("}", "")
        s = s.replace("\x00", "\\").replace("\x01", "{").replace("\x02", "}")
    s = re.sub(r"[ \t]+", " ", s)
    return "\n".join(x.strip() for x in s.splitlines() if x.strip())


def _ler_dxf_bytes(path: Path) -> str:
    data = path.read_bytes()
    if data[:22] == b"AutoCAD Binary DXF\r\n\x1a\x00":
        raise ValueError("DXF binário não suportado; salve como DXF ASCII")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


ENTIDADES_TEXTO = {"TEXT", "MTEXT", "ATTRIB", "ACAD_TABLE", "MULTILEADER", "MLEADER", "DIMENSION", "RTEXT"}


def textos_dxf(path: Path) -> dict:
    """Varre o DXF (pares código/valor) e coleta textos soltos e atributos de blocos.

    Não depende de um parser completo: desenhos convertidos às vezes têm tabelas
    internas fora do padrão que derrubam bibliotecas mais rígidas.
    """
    linhas = _ler_dxf_bytes(path).splitlines()
    textos: list[str] = []
    blocos: list[str] = []
    bloco_atual: list[str] = []
    nome_insert = None
    ent = None
    partes: list[str] = []
    tag = None
    secao = None
    esperando_nome_secao = False

    def fechar_entidade():
        nonlocal partes, tag
        if ent in ENTIDADES_TEXTO and partes:
            if ent == "MTEXT":
                bruto = "".join(partes)  # códigos 3 (continuação) + 1 (final)
                t = limpar_texto_cad(bruto, mtext=True)
            elif ent in ("ACAD_TABLE", "MULTILEADER", "MLEADER"):
                t = " | ".join(x for x in (limpar_texto_cad(p, mtext=True) for p in partes) if x)
            else:
                t = limpar_texto_cad(partes[-1], mtext=ent == "DIMENSION")
                if ent == "DIMENSION" and ("<>" in t or not t):
                    t = ""
            if ent == "ATTRIB":
                if t and nome_insert is not None:
                    bloco_atual.append(f"{tag}={t}" if tag else t)
            elif t:
                textos.append(t)
        partes, tag = [], None

    def fechar_insert():
        nonlocal bloco_atual, nome_insert
        if nome_insert is not None and bloco_atual:
            blocos.append(f"{nome_insert}: " + "; ".join(bloco_atual))
        bloco_atual, nome_insert = [], None

    for i in range(0, len(linhas) - 1, 2):
        codigo = linhas[i].strip()
        valor = linhas[i + 1]
        if codigo == "0":
            fechar_entidade()
            ent = valor.strip()
            if ent != "ATTRIB":  # ATTRIBs seguem o INSERT até o SEQEND; qualquer outra entidade fecha o bloco
                fechar_insert()
            if ent == "SECTION":
                esperando_nome_secao = True
            elif ent == "ENDSEC":
                secao = None
            continue
        if esperando_nome_secao and codigo == "2":
            secao, esperando_nome_secao = valor.strip(), False
            continue
        if secao not in ("ENTITIES", "BLOCKS"):
            continue
        if ent == "INSERT" and codigo == "2":
            nome_insert = valor.strip()
        elif ent in ENTIDADES_TEXTO:
            if codigo == "1" or (ent == "MTEXT" and codigo == "3") or \
                    (ent == "ACAD_TABLE" and codigo in ("302", "303")) or \
                    (ent in ("MULTILEADER", "MLEADER") and codigo == "304"):
                partes.append(valor)
            elif ent == "ATTRIB" and codigo == "2":
                tag = valor.strip()
    fechar_entidade()
    fechar_insert()

    def util(t: str) -> bool:
        return len(t) > 1 and not re.fullmatch(r"[\W\d_]{0,3}", t)

    return {
        "textos": list(dict.fromkeys(t for t in textos if util(t))),
        "blocos": list(dict.fromkeys(blocos)),
    }


def trechos_dxf(path: Path) -> list[Trecho]:
    dados = textos_dxf(path)
    linhas: list[tuple[int, str]] = []
    n = 0
    if dados["textos"]:
        n += 1
        linhas.append((n, "TEXTOS DO DESENHO (sem posição; a ordem é a do arquivo):"))
        for t in dados["textos"]:
            n += 1
            linhas.append((n, t.replace("\n", " / ")))
    if dados["blocos"]:
        n += 1
        linhas.append((n, "BLOCOS COM ATRIBUTOS (símbolo: atributo=valor):"))
        for b in dados["blocos"]:
            n += 1
            linhas.append((n, b))
    return _agrupar(linhas, lambda a, b: f"desenho, itens {a}-{b}")


def trechos_dwg(path: Path) -> list[Trecho]:
    with tempfile.TemporaryDirectory(prefix="wia_dwg_") as tmp:
        dxf = dwg_para_dxf(path, Path(tmp))
        return trechos_dxf(dxf)
