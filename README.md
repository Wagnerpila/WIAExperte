# WIA Expert: Assistente de Automação e Instrumentação

Assistente web para técnicos de automação e instrumentação. Ele conversa usando o Claude e consulta uma base de conhecimento da planta: backups exportados, logs, fotos, áudios, WhatsApp, manuais e ocorrências. Tem também um painel de indicadores de base, confiabilidade, alarmes e uso.

- **Chat**: o assistente segue o prompt em `prompts/system_prompt.md` (fato / dedução / hipótese / conhecimento geral, segurança em primeiro lugar). Ele busca na base antes de responder e cita as fontes. Aceita fotos pela câmera do celular.
- **Base**: upload de arquivos com detecção de tipo, extração de texto, indexação para busca (SQLite FTS5) e controle de versões por hash.
- **Painel**: indicadores calculados a partir do que existe na base (detalhes abaixo). Funciona com a base vazia e vai se preenchendo.
- **Ocorrências, glossário e notas**: o histórico estruturado que alimenta o painel e a memória do assistente.

## Rodar localmente (Windows)

```powershell
cd wia-expert
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # preencha ANTHROPIC_API_KEY e use WIA_COOKIE_SECURE=false
.\.venv\Scripts\python -m app.cli criar-usuario seu.nome --admin
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Abra http://localhost:8000.

Testes (não chamam a API): `.\.venv\Scripts\python -m pytest -q`

## Deploy no Easypanel (forma usada em produção)

App do tipo **App** com origem neste repositório (branch `main`), construído pelo `Dockerfile`:

- Porta do domínio: `8000`.
- Volume (montagem) em `/data`, onde ficam o banco e os arquivos.
- Variáveis de ambiente: as do `.env.example`, sem `WIA_DOMINIO`. O HTTPS é feito pelo Traefik do Easypanel.
- Deploy automático: cada `git push` na `main` roda os testes no GitHub Actions (`.github/workflows/deploy.yml`) e, se passarem, chama o gatilho de implantação do Easypanel. A URL do gatilho fica no secret `EASYPANEL_DEPLOY_URL` do repositório (Settings → Secrets and variables → Actions), nunca no código.

Criar usuário no container: `docker exec -it $(docker ps -qf name=n8n_wia-expert) python -m app.cli criar-usuario seu.nome --admin`

## Deploy numa VPS sem painel (Docker + Caddy com HTTPS automático)

Use esta opção só numa VPS que não tenha outro proxy ocupando as portas 80/443.

1. Aponte um domínio (registro A) para o IP da VPS e libere as portas 80 e 443.
2. Copie a pasta `wia-expert` para o servidor e crie o `.env` a partir do `.env.example`, com `WIA_DOMINIO`, `ANTHROPIC_API_KEY`, `WIA_SECRET_KEY` e `WIA_COOKIE_SECURE=true`.
3. Suba o serviço:
   ```bash
   mkdir -p data && sudo chown 1000:1000 data
   docker compose up -d --build
   docker compose exec app python -m app.cli criar-usuario seu.nome --admin
   ```
4. Acesse `https://SEU_DOMINIO`.

**Backup**: tudo fica em `data/` (banco `wia.db` + pasta `arquivos/`). Faça backup diário dessa pasta, por exemplo com `sqlite3 data/wia.db ".backup data/wia-copia.db"` e um `rsync` para outro lugar.

> **Atenção (dados da empresa na nuvem):** backups de CLP, logs e conversas do WhatsApp vão ficar numa VPS fora da rede da empresa, e o texto das consultas é enviado à API da Anthropic. Confirme com a TI/Segurança da Informação do cliente antes de subir material real. As conversas do WhatsApp têm dados pessoais de colegas (LGPD): combine com o grupo e suba só o necessário.

## O que subir e como exportar

| Material | Como exportar | Tipo no upload |
|---|---|---|
| Manuais, P&IDs, descritivos, listas de I/O | PDF com texto (PDF escaneado precisa de OCR antes) | Documentação |
| Documentos de projeto (listas de I/O, cabos, materiais) | Planilhas `.xlsx`/`.xlsm`/`.xls` como vieram: o cabeçalho (inclusive grupos mesclados) é detectado e cada linha vira "Coluna: valor"; as abas auxiliares do modelo Klabin (Status, Areas, Disciplinas, Tipodoc) são ignoradas e a Capa vira a ficha | Documentação |
| Desenhos AutoCAD | `.dwg` (lido com a LibreDWG `dwg2dxf`, já incluída na imagem Docker) ou `.dxf`: textos, MTEXT, tabelas e atributos de blocos (carimbo, tags). Sem posição na folha | Documentação |
| Desenhos Visio | `.vsdx`: texto e dados de forma (código do item, fornecedor, tags) por página | Documentação |
| Programa Siemens (TIA Portal) | Exportar blocos como fonte (SCL/STL) ou XML via Openness; tabelas de tags em XLSX → CSV | Backup |
| Programa Schneider (Control Expert) | Exportar seções/DFBs em `.xef`/`.zef` (o `.xef` é XML legível); variáveis em `.xsy`/CSV | Backup |
| Valmet DNA | Relatórios e listas do DNA Explorer, impressões/PDF dos diagramas do FbCAD, listas de tags | Backup / Documentação |
| Rockwell | `.L5X` (exportação XML do Studio 5000) | Backup |
| Arquivos de projeto binários (`.ap17`, `.stu`, `.acd`…) | Ficam guardados com controle de versão, mas a lógica só é lida a partir da exportação em texto | Backup |
| Buffer de diagnóstico / logs | Exportar em CSV/TXT pelo Online & Diagnostics ou pela ferramenta da plataforma | Log |
| Histórico de alarmes/eventos | CSV com data/hora e tag (estado e prioridade, se houver). Veja `/static/modelos/alarmes_modelo.csv` | CSV de alarmes |
| Histórico de manutenção | CSV com data, equipamento e sintoma (causa, categoria, tempo de parada…). Veja `/static/modelos/ocorrencias_modelo.csv` | CSV de ocorrências |
| Grupo do WhatsApp | No celular: grupo → ⋮ → Mais → Exportar conversa → Incluir mídia → `.zip`. Ou JSON do WAHA | WhatsApp |
| Fotos | Pelo chat (câmera) ou pela aba Base; descritas automaticamente pelo Claude ao entrar | Foto |
| Áudios e vídeos | Ficam "pendentes" até receber transcrição: suba um `.txt` com a transcrição, ou instale `faster-whisper` para transcrever áudio no servidor | Áudio / Vídeo |

O CSV de alarmes reconhece colunas com nomes comuns em português e inglês (`Data/Hora`, `Timestamp`, `Tag`, `Point`, `Message`, `Priority`, `State`…), separador `;` ou `,` e datas `dd/mm/aaaa` ou ISO.

## GreenDocs (busca sob demanda pelo chat)

Nada é baixado em massa. Quando a base não tem a resposta, o assistente usa `buscar_greendocs` (busca no ambiente PUMA, inclusive dentro do conteúdo dos arquivos, por exemplo por uma tag) e `baixar_greendocs` (baixa até 5 documentos por chamada, processa na hora e depois responde a partir da base). A revisão baixada fica registrada; o assistente não baixa de novo o que já está na base na mesma revisão.

- Ligar: no Ambiente do Easypanel, `WIA_GREENDOCS_EMAIL` e `WIA_GREENDOCS_SENHA` (de preferência uma conta de serviço). Sem essas variáveis, as ferramentas nem aparecem para o assistente.
- Funciona com um Chromium sem tela (Playwright) dentro do container; a sessão fica em `/data/greendocs_sessao.json` para não fazer login a cada pergunta.
- Testar pelo terminal: `python -m app.cli greendocs-buscar "34221RNF-30101-VT1"` e `python -m app.cli greendocs-baixar 34231-I-LIO-00117-0001`.
- Fora do Docker: `pip install -r requirements.txt` e `python -m playwright install --only-shell chromium`.

## Remotas e endereçamento de I/O

A aba **Remotas** é um dashboard do endereçamento: indicadores (remotas, pontos, ocupação geral, canais livres, remotas ≥ 90%, abaixo da reserva), pontos por área e por tipo de sinal, faixas de ocupação, cards de cada remota (anel de ocupação, composição DI/DO/AI/AO/RTD-TC) e o detalhe com capacidade instalada, mapa de cartões e a lista de pontos. No chat, a ferramenta `endereco_io` mostra cada ponto como um card com a cadeia caixa de junção → remota → cartão → canal → controlador.

Fontes (tabela `io_pontos`):
- **Cadastro RemotasPuma**: botão *Importar cadastro* na aba Remotas com o `data.js` do projeto RemotasPuma (ou `python -m app.cli importar-remotas data.js`). Traz as 230 remotas da Unidade Puma com capacidade instalada e reserva. Reimportar substitui o cadastro anterior.
- **Listas de I/O em planilha da base**: toda planilha que entra na base passa pelo leitor de I/O, que reconhece pelo cabeçalho as colunas de tag, remota/gabinete, cartão/slot, canal, borneira, caixa de junção e borne, controlador e tipo. As que já estavam na base são lidas sozinhas na inicialização; *Reler planilhas* (ou `python -m app.cli indexar-io --todos`) relê todas. Um ponto que aparece em mais de uma lista conta uma vez só, com as duas fontes.

Os dados da planta ficam só no volume `/data`; nada disso vai para o repositório.

## Indicadores do painel

**Base**: documentos por tipo, plataforma e área; envios por semana; pendências (erro, PDF sem texto, aguardando transcrição, binário).

**Ocorrências** (seguem definições usuais de manutenção, como SMRP):
- **MTTR**: tempo médio de parada por ocorrência, contando só as que têm o tempo informado. Inclui espera, porque é o tempo informado pelo técnico.
- **MTBF da planta**: período ÷ número de ocorrências. **MTBF por equipamento**: média dos intervalos entre ocorrências do mesmo equipamento.
- **Pareto** de tempo de parada com percentual acumulado, *bad actors* por número de ocorrências, distribuição por categoria de causa, área e plataforma, tendência mensal, falhas repetidas.
- **Lacunas**: equipamentos com ocorrência e sem backup, documentação ou logs na base.

**Alarmes** (referências EEMUA 191 / ISA-18.2 / IEC 62682, obtidas de fontes secundárias e só orientativas; vale a filosofia de alarmes da planta):

| Indicador | Referência usada |
|---|---|
| Média de alarmes por 10 min | ≈1 aceitável; >2 sobrecarga |
| Pico em 10 min / inundação | >10 alarmes em 10 min = flood; <1% do tempo em flood |
| Standing (ativo >24 h) | <5 (IEC 62682); EEMUA cita <10 |
| Top 10 alarmes | devem somar menos de ~20% do total |
| Chattering | 3 ou mais ativações da mesma tag em 60 s (critério da ferramenta) |

**Uso**: respostas, chamadas à API, custo estimado (preços configuráveis no `.env`), percentual de entrada vinda do cache, tempo médio, ferramentas usadas e buscas sem resultado (mostram o que falta subir).

## Estrutura

```
app/
  main.py      API web (FastAPI), sessão e uploads
  agent.py     loop de ferramentas do Claude (buscar_base, ler_documento, listar_documentos,
               consultar_ocorrencias, consultar_alarmes, salvar_nota, registrar_ocorrencia, glossario)
  ingest.py    leitura de PDF/texto/WhatsApp/WAHA/ZIP/CSV, trechos, versões
  leitores.py  planilhas (xlsx/xls), Visio (vsdx), AutoCAD (dwg/dxf) e código de documento Klabin
  painel.py    cálculo dos indicadores
  llm.py       cliente Anthropic, descrição de fotos, registro de uso
  db.py        esquema SQLite + FTS5
  cli.py       criar usuário, importar pasta, reprocessar pendentes
  static/      interface (HTML/CSS/JS sem build)
prompts/system_prompt.md   prompt de sistema (v2 + seção de ferramentas)
```

Importar uma pasta inteira de uma vez: `python -m app.cli importar "D:\\Backups\\Caldeira" --tipo backup --plataforma "Valmet DNA" --area "Caldeira de recuperação"`.

Pela interface (aba Base) dá para mandar uma pasta inteira: botão "Escolher uma pasta inteira" ou arrastar a pasta para a área de envio. Todas as subpastas são percorridas, o lixo do Windows/Office (`Thumbs.db`, `desktop.ini`, `~$…`) é descartado e o envio vai em lotes de até 10 arquivos / 40 MB, com progresso e nova tentativa só dos lotes que falharem. Arquivos acima de 32 MB (ex.: o `.zip` do GreenDocs) sobem em partes de 8 MB por `/api/envios` e retomam do ponto onde pararam se a conexão cair; o limite por arquivo é `WIA_MAX_UPLOAD_MB` (padrão 2048) e o servidor recusa o envio se não houver espaço em disco para o arquivo e a extração. PDFs são lidos com o PDFium (`pypdfium2`; um manual de 3.400 páginas leva ~25 s), com o `pypdf` como reserva. Arquivos `.zip` (inclusive com subpastas e zip dentro de zip, até 4 níveis) são abertos no servidor; cada arquivo extraído guarda na observação de qual zip e subpasta veio, e o próprio zip vira um índice do seu conteúdo. Nomes com acento de zips do Windows são corrigidos; zip com senha dá erro explicando que precisa ser descompactado antes.

Documentos com código Klabin no nome (ex. `34231-I-LIO-00117-0001.xlsx`) ganham uma ficha (máquina — 341xx = MP27, 342xx = MP28 —, área, disciplina, tipo) e um rótulo em cada trecho, e a área do documento é preenchida sozinha. Arquivos com conteúdo idêntico a um já indexado (ex.: o mesmo PDF solto e dentro de um `.zip`) ficam com status `duplicado` e não são indexados de novo. Para ler `.dwg` fora do Docker, instale a LibreDWG (`dwg2dxf` no PATH); sem ela o DWG fica `pendente` e pode ser reprocessado depois.

## Decisões técnicas

- Modelo `claude-opus-5-5` com esforço `high`, cache de prompt automático e *fallback* do servidor em caso de recusa do filtro de segurança (`WIA_USE_FALLBACKS`).
- O histórico enviado à API nunca é editado, só recebe novas mensagens no fim. Isso mantém o cache e os blocos de raciocínio válidos.
- Conteúdo da base volta ao modelo marcado como "dado, não instrução" (proteção contra instruções escondidas em mensagens/arquivos).
- A busca usa texto completo (BM25), o que é bom para tags e códigos exatos. Se a base crescer muito e as buscas por sinônimo ficarem fracas, o próximo passo é acrescentar busca semântica (embeddings).
