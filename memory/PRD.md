# Adam AI — PRD

## Problema original
Construir "Adam", IA própria estilo ChatGPT/Claude/Manus, criada por Felipe Hudson (19 anos, Belo Horizonte, BR). Chat, memória, arquivos, geração de PDF/Word/Excel/código/sites, visão, geração de imagens, pesquisa web, controle de navegador, voz, API/plugin para integração, rede neural própria.

## Escolhas do usuário
- Cérebro: modelo forte agora (Claude Sonnet 4.5 via chave universal), trocável por servidor local (Ollama) + mini rede neural própria treinada do zero (PyTorch).
- V1: chat c/ memória e histórico, ler arquivos/imagens, gerar arquivos, gerar imagens.
- Login: email + senha (Clerk gerenciado pela Emergent).
- API própria com chaves.
- Usuário NÃO quer testing agent/reviewer — ele mesmo testa.

## Arquitetura
- Backend FastAPI (server.py), neural.py (AdamNano Transformer 0.83M params, tokenizer de caracteres próprio, pesos salvos no Mongo), files_util.py (PDF/DOCX/XLSX/código + extração), web_tools.py (pesquisa via Brave HTML, leitura de sites com proteção SSRF), storage.py (Emergent Object Storage), clerk_auth.py.
- Frontend React: Chat (SSE streaming, seletor de motor, anexos, cartões de arquivos/imagens/links), Memória, Arquivos, Laboratório Neural, API & Chaves, Configurações.
- Contexto: últimas 16 mensagens + resumo progressivo automático das antigas (não estoura janela de contexto).

## Implementado (2026-11-21)
- Chat streaming com 3 motores (pro / neural / local Ollama), histórico, renomear/excluir.
- Ferramentas do Adam: criar_arquivo, gerar_imagem (Gemini Nano Banana), salvar_memoria, pesquisar_web, abrir_site, abrir_no_navegador (abre aba no navegador do usuário).
- Upload: imagens (visão), PDF, DOCX, XLSX, texto/código.
- API v1: POST /api/v1/chat, GET /api/v1/files/{id} com chaves adam_live_ (hash sha256).
- Laboratório: treinar, curva de loss, gerar texto, adicionar dados ao dataset.

## Backlog
- P0: Agente local (Adam Edge Agent – extensão Chrome/app desktop) para clicar/digitar no navegador e computador do usuário com permissão.
- P1: Voz (STT/TTS), edição de imagens, preview ao vivo de sites gerados, execução de Python em sandbox.
- P1: API de busca dedicada (pesquisa atual é scraping e pode ser limitada).
- P2: Plugin/widget embutível, fine-tuning com GPU, tokenizer BPE, modelo maior.
