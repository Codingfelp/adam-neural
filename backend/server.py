import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import httpx
from bson import Binary
from emergentintegrations.llm.chat import ImageContent, LlmChat, StreamDone, TextDelta, ToolCallReady, ToolCallStart, UserMessage
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel
from starlette.middleware.cors import CORSMiddleware

try:
    from backend.clerk_auth import clerk_router, require_user
    from backend import storage, files_util
    from backend.neural import engine as neural
except ModuleNotFoundError:
    from clerk_auth import clerk_router, require_user
    import storage
    import files_util
    from neural import engine as neural

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]
LLM_KEY = os.environ["EMERGENT_LLM_KEY"]
PRO_MODEL = ("anthropic", "claude-sonnet-4-5-20250929")
IMAGE_MODEL = "gemini-3.1-flash-image-preview"
logger = logging.getLogger("adam")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

INDEXES: dict = {
    "conversations": [IndexModel([("id", ASCENDING)], name="id", unique=True),
                      IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)], name="user_updated")],
    "messages": [IndexModel([("conversation_id", ASCENDING), ("created_at", ASCENDING)], name="conv_created")],
    "files": [IndexModel([("id", ASCENDING)], name="id", unique=True)],
    "memories": [IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created")],
    "api_keys": [IndexModel([("hash", ASCENDING)], name="hash", unique=True),
                 IndexModel([("user_id", ASCENDING)], name="user")],
    "settings": [IndexModel([("user_id", ASCENDING)], name="user", unique=True)],
}


async def ensure_indexes() -> None:
    for collection, models in INDEXES.items():
        for model in models:
            try:
                await db[collection].create_indexes([model])
            except Exception as exc:
                logger.error("ensure_indexes(%s.%s): %s", collection, model.document["name"], exc)


app = FastAPI(title="Adam AI")
api = APIRouter(prefix="/api")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Models ----------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=50000)
    conversation_id: Optional[str] = None
    engine: str = "pro"
    attachment_ids: List[str] = []


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MemoryIn(BaseModel):
    fact: str = Field(min_length=2, max_length=1000)
    tag: str = "geral"


class SettingsIn(BaseModel):
    ollama_url: str = ""
    ollama_model: str = "llama3.1"
    default_engine: str = "pro"
    response_style: str = ""


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class TrainIn(BaseModel):
    steps: int = Field(default=200, ge=10, le=2000)


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    temperature: float = Field(default=0.8, ge=0.1, le=2.0)
    max_tokens: int = Field(default=300, ge=10, le=1000)


class CorpusIn(BaseModel):
    text: str = Field(min_length=20, max_length=200000)


class V1ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=50000)
    conversation_id: Optional[str] = None
    engine: str = "pro"


# ---------- Prompting ----------
SYSTEM_PROMPT = """Você é Adam, uma inteligência artificial própria criada por Felipe Hudson, um jovem brilhante de 19 anos nascido em Belo Horizonte, Minas Gerais, Brasil.
Se perguntarem quem te criou, responda com orgulho: Felipe Hudson. Nunca diga que foi criado pela OpenAI, Anthropic, Google ou outra empresa.
Responda no idioma do usuário (padrão: português do Brasil), de forma clara, inteligente e acolhedora. Use Markdown, tabelas e blocos de código com a linguagem indicada.

Ferramentas disponíveis:
- criar_arquivo: SEMPRE que o usuário pedir um documento, planilha, script, site ou qualquer arquivo para baixar, chame esta ferramenta (pdf, docx, xlsx, py, js, html, css, json, md, csv, txt, etc.). Para sites, gere um único arquivo .html completo com CSS e JS embutidos.
  - pdf/docx: conteúdo em texto com títulos "# ", "## ", listas "- " e **negrito**.
  - xlsx: conteúdo em CSV (primeira linha = cabeçalho).
- gerar_imagem: quando o usuário pedir para criar/desenhar/gerar uma imagem. Escreva o prompt em inglês, detalhado.
- salvar_memoria: quando o usuário contar algo importante e duradouro sobre si, preferências ou projetos.
Depois de usar uma ferramenta, comente brevemente o resultado. Nunca insira imagens ou links de arquivos em Markdown: os arquivos e imagens criados já aparecem automaticamente para o usuário com botão de download. Seja honesto sobre limitações: nesta versão você ainda não controla o computador/navegador do usuário nem pesquisa na web em tempo real."""

TOOLS = [
    {"type": "function", "function": {
        "name": "criar_arquivo",
        "description": "Cria um arquivo para o usuário baixar (pdf, docx, xlsx, py, js, html, css, json, md, csv, txt...).",
        "parameters": {"type": "object", "properties": {
            "nome_arquivo": {"type": "string", "description": "Nome com extensão, ex: relatorio.pdf"},
            "conteudo": {"type": "string", "description": "Conteúdo completo do arquivo"}},
            "required": ["nome_arquivo", "conteudo"]}}},
    {"type": "function", "function": {
        "name": "gerar_imagem",
        "description": "Gera uma imagem a partir de uma descrição.",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "Descrição detalhada em inglês"}},
            "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "salvar_memoria",
        "description": "Salva um fato duradouro sobre o usuário na memória de longo prazo.",
        "parameters": {"type": "object", "properties": {
            "fato": {"type": "string"}, "tag": {"type": "string", "description": "preferencia, projeto, pessoal, tecnologia..."}},
            "required": ["fato"]}}},
]
TOOL_LABELS = {"criar_arquivo": "Criando arquivo", "gerar_imagem": "Gerando imagem", "salvar_memoria": "Salvando na memória"}


async def build_system(user_id: str) -> str:
    mems = await db.memories.find({"user_id": user_id}, {"_id": 0, "fact": 1}).sort("created_at", -1).to_list(50)
    st = await db.settings.find_one({"user_id": user_id}, {"_id": 0}) or {}
    extra = ""
    if mems:
        extra += "\n\nMemória de longo prazo sobre o usuário:\n" + "\n".join(f"- {m['fact']}" for m in mems)
    if st.get("response_style"):
        extra += f"\n\nPreferência de estilo do usuário: {st['response_style']}"
    return SYSTEM_PROMPT + extra


# ---------- Files ----------
def file_public(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("id", "name", "mime", "size", "kind", "prompt", "created_at")}


async def save_file(user_id: str, name: str, data: bytes, mime: str, kind: str, **extra) -> dict:
    fid = str(uuid.uuid4())
    path = f"{storage.APP_NAME}/{kind}/{user_id}/{fid}.{files_util.ext_of(name)}"
    res = await asyncio.to_thread(storage.put_object, path, data, mime)
    rec = {"id": fid, "user_id": user_id, "name": name, "mime": mime, "size": len(data), "kind": kind,
           "storage_path": res["path"], "is_deleted": False, "created_at": now(), **extra}
    await db.files.insert_one(rec)
    return file_public(rec)


async def generate_image(user_id: str, prompt: str) -> dict:
    chat = LlmChat(api_key=LLM_KEY, session_id=str(uuid.uuid4()), system_message="You are an image generator.")
    chat.with_model("gemini", IMAGE_MODEL).with_params(modalities=["image", "text"])
    _, images = await chat.send_message_multimodal_response(UserMessage(text=prompt))
    if not images:
        raise RuntimeError("Nenhuma imagem retornada")
    img = images[0]
    data = base64.b64decode(img["data"])
    ext = "png" if "png" in img.get("mime_type", "png") else "jpg"
    return await save_file(user_id, f"adam-vision-{uuid.uuid4().hex[:6]}.{ext}", data, img.get("mime_type", "image/png"), "image", prompt=prompt)


async def dispatch_tool(user_id: str, name: str, args: dict):
    if name == "criar_arquivo":
        fname = (args.get("nome_arquivo") or "arquivo.txt").strip().replace("/", "_")
        data, mime = await asyncio.to_thread(files_util.build_file, fname, args.get("conteudo", ""))
        f = await save_file(user_id, fname, data, mime, "generated")
        return {"ok": True, "arquivo": fname, "tamanho_bytes": len(data)}, f
    if name == "gerar_imagem":
        f = await generate_image(user_id, args.get("prompt", ""))
        return {"ok": True, "imagem": f["name"]}, f
    if name == "salvar_memoria":
        await db.memories.insert_one({"id": str(uuid.uuid4()), "user_id": user_id, "fact": args.get("fato", "")[:1000],
                                      "tag": args.get("tag") or "geral", "source": "adam", "created_at": now()})
        return {"ok": True}, None
    return {"ok": False, "erro": "ferramenta desconhecida"}, None


# ---------- Engines ----------
async def run_pro(user_id, system, history, text, images):
    initial = [{"role": "system", "content": system}] + history
    chat = (LlmChat(api_key=LLM_KEY, session_id=str(uuid.uuid4()), system_message=system, initial_messages=initial)
            .with_model(*PRO_MODEL).with_tools(TOOLS, tool_choice="auto"))
    user_msg = UserMessage(text=text, file_contents=[ImageContent(image_base64=b) for b in images])
    for _ in range(6):
        pending = []
        async for ev in chat.stream_message(user_msg):
            if isinstance(ev, TextDelta):
                yield {"type": "delta", "content": ev.content}
            elif isinstance(ev, ToolCallStart):
                yield {"type": "tool", "name": ev.name, "label": TOOL_LABELS.get(ev.name, ev.name)}
            elif isinstance(ev, ToolCallReady):
                pending.append(ev.tool_call)
            elif isinstance(ev, StreamDone):
                break
        if not pending:
            return
        for tc in pending:
            args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments or "{}")
            try:
                result, f = await dispatch_tool(user_id, tc.name, args)
                if f:
                    yield {"type": "file", "file": f}
            except Exception as e:
                logger.exception("tool %s failed", tc.name)
                result = {"ok": False, "erro": str(e)[:300]}
            chat.add_tool_result(tc.id, json.dumps(result, ensure_ascii=False))
        user_msg = None
        yield {"type": "delta", "content": "\n\n"}


async def run_neural(history, text):
    ctx = "".join(f"{'Usuário' if m['role'] == 'user' else 'Adam'}: {m['content'][:300]}\n" for m in history[-4:])
    prompt = f"{ctx}Usuário: {text[:500]}\nAdam:"
    out = await asyncio.to_thread(neural.generate, prompt, 320, 0.7, "\nUsuário:")
    out = out.strip() or "(A rede neural ainda está aprendendo. Treine mais épocas no Laboratório Neural.)"
    for i in range(0, len(out), 4):
        yield {"type": "delta", "content": out[i:i + 4]}
        await asyncio.sleep(0.01)


async def run_local(user_id, system, history, text):
    st = await db.settings.find_one({"user_id": user_id}, {"_id": 0}) or {}
    url = (st.get("ollama_url") or "").rstrip("/")
    if not url:
        yield {"type": "delta", "content": "Nenhum servidor local configurado. Vá em **Configurações** e informe a URL do seu servidor Ollama (ex: `http://seu-servidor:11434`)."}
        return
    msgs = [{"role": "system", "content": system}] + history + [{"role": "user", "content": text}]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=8)) as hc:
            async with hc.stream("POST", f"{url}/api/chat", json={"model": st.get("ollama_model") or "llama3.1", "messages": msgs, "stream": True}) as r:
                if r.status_code != 200:
                    yield {"type": "delta", "content": f"Servidor local respondeu com erro {r.status_code}."}
                    return
                async for line in r.aiter_lines():
                    if line.strip():
                        piece = json.loads(line).get("message", {}).get("content", "")
                        if piece:
                            yield {"type": "delta", "content": piece}
    except Exception as e:
        yield {"type": "delta", "content": f"Não consegui conectar ao servidor local `{url}`: {str(e)[:200]}"}


async def chat_turn(user_id: str, body: ChatIn):
    conv = None
    if body.conversation_id:
        conv = await db.conversations.find_one({"id": body.conversation_id, "user_id": user_id}, {"_id": 0})
        if not conv:
            yield {"type": "error", "message": "Conversa não encontrada"}
            return
    if not conv:
        conv = {"id": str(uuid.uuid4()), "user_id": user_id, "title": body.message.strip()[:60], "engine": body.engine,
                "created_at": now(), "updated_at": now()}
        await db.conversations.insert_one(dict(conv))
    cid = conv["id"]
    yield {"type": "meta", "conversation_id": cid, "title": conv["title"]}

    past = await db.messages.find({"conversation_id": cid}, {"_id": 0}).sort("created_at", 1).to_list(400)
    history = [{"role": m["role"], "content": m.get("model_text") or m["content"]} for m in past[-30:] if (m.get("model_text") or m["content"]).strip()]

    attachments, images, doc_texts = [], [], []
    if body.attachment_ids:
        recs = await db.files.find({"id": {"$in": body.attachment_ids[:10]}, "user_id": user_id, "is_deleted": False}, {"_id": 0}).to_list(10)
        for r in recs:
            attachments.append(file_public(r))
            if r["mime"].startswith("image/"):
                data, _ = await asyncio.to_thread(storage.get_object, r["storage_path"])
                images.append(await asyncio.to_thread(files_util.image_to_jpeg_b64, data))
            elif r.get("extracted_text"):
                doc_texts.append(f"\n\n[Arquivo anexado: {r['name']}]\n{r['extracted_text'][:30000]}")
    model_text = body.message + "".join(doc_texts)
    if images and body.engine == "pro":
        model_text += f"\n\n[{len(images)} imagem(ns) anexada(s)]"

    await db.messages.insert_one({"id": str(uuid.uuid4()), "conversation_id": cid, "user_id": user_id, "role": "user",
                                  "content": body.message, "model_text": model_text, "attachments": attachments,
                                  "files": [], "engine": body.engine, "created_at": now()})
    system = await build_system(user_id)
    parts, files = [], []
    try:
        if body.engine == "neural":
            gen = run_neural(history, body.message)
        elif body.engine == "local":
            gen = run_local(user_id, system, history, model_text)
        else:
            gen = run_pro(user_id, system, history, model_text, images)
        async for ev in gen:
            if ev["type"] == "delta":
                parts.append(ev["content"])
            elif ev["type"] == "file":
                files.append(ev["file"])
            yield ev
    except Exception as e:
        logger.exception("chat failed")
        msg = f"\n\n⚠️ Erro ao gerar resposta: {str(e)[:200]}"
        parts.append(msg)
        yield {"type": "delta", "content": msg}
    mid = str(uuid.uuid4())
    await db.messages.insert_one({"id": mid, "conversation_id": cid, "user_id": user_id, "role": "assistant",
                                  "content": "".join(parts).strip(), "attachments": [], "files": files,
                                  "engine": body.engine, "created_at": now()})
    await db.conversations.update_one({"id": cid}, {"$set": {"updated_at": now(), "engine": body.engine}})
    yield {"type": "done", "message_id": mid, "conversation_id": cid}


# ---------- Routes: chat ----------
@api.get("/")
async def root():
    return {"name": "Adam", "creator": "Felipe Hudson", "status": "online"}


@api.get("/me")
async def me(user: dict = Depends(require_user)):
    return {"user_id": user["user_id"]}


@api.post("/chat/stream")
async def chat_stream(body: ChatIn, user: dict = Depends(require_user)):
    async def sse():
        async for ev in chat_turn(user["user_id"], body):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.get("/conversations")
async def list_conversations(user: dict = Depends(require_user)):
    return await db.conversations.find({"user_id": user["user_id"]}, {"_id": 0, "user_id": 0}).sort("updated_at", -1).to_list(300)


@api.get("/conversations/{cid}")
async def get_conversation(cid: str, user: dict = Depends(require_user)):
    conv = await db.conversations.find_one({"id": cid, "user_id": user["user_id"]}, {"_id": 0, "user_id": 0})
    if not conv:
        raise HTTPException(404, "Conversa não encontrada")
    msgs = await db.messages.find({"conversation_id": cid}, {"_id": 0, "model_text": 0, "user_id": 0}).sort("created_at", 1).to_list(1000)
    return {"conversation": conv, "messages": msgs}


@api.patch("/conversations/{cid}")
async def rename_conversation(cid: str, body: TitleIn, user: dict = Depends(require_user)):
    r = await db.conversations.update_one({"id": cid, "user_id": user["user_id"]}, {"$set": {"title": body.title}})
    if not r.matched_count:
        raise HTTPException(404, "Conversa não encontrada")
    return {"ok": True}


@api.delete("/conversations/{cid}")
async def delete_conversation(cid: str, user: dict = Depends(require_user)):
    r = await db.conversations.delete_one({"id": cid, "user_id": user["user_id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Conversa não encontrada")
    await db.messages.delete_many({"conversation_id": cid})
    return {"ok": True}


# ---------- Routes: files ----------
MAX_UPLOAD = 20 * 1024 * 1024


@api.post("/files/upload")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(require_user)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Arquivo maior que 20 MB")
    mime = file.content_type or "application/octet-stream"
    name = file.filename or "arquivo"
    extracted = "" if mime.startswith("image/") else await asyncio.to_thread(files_util.extract_text, data, name, mime)
    if not mime.startswith("image/") and not extracted.strip():
        raise HTTPException(415, "Formato não suportado. Envie imagens, PDF, DOCX, XLSX ou arquivos de texto/código.")
    return await save_file(user["user_id"], name, data, mime, "uploads", extracted_text=extracted[:100000])


@api.get("/files")
async def list_files(user: dict = Depends(require_user)):
    recs = await db.files.find({"user_id": user["user_id"], "is_deleted": False, "kind": {"$in": ["generated", "image"]}},
                               {"_id": 0}).sort("created_at", -1).to_list(200)
    return [file_public(r) for r in recs]


@api.get("/files/{fid}")
async def download_file(fid: str, user: dict = Depends(require_user)):
    rec = await db.files.find_one({"id": fid, "user_id": user["user_id"], "is_deleted": False}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Arquivo não encontrado")
    data, _ = await asyncio.to_thread(storage.get_object, rec["storage_path"])
    return Response(content=data, media_type=rec["mime"], headers={"Content-Disposition": f'inline; filename="{rec["name"]}"'})


# ---------- Routes: memory & settings ----------
@api.get("/memories")
async def list_memories(user: dict = Depends(require_user)):
    return await db.memories.find({"user_id": user["user_id"]}, {"_id": 0, "user_id": 0}).sort("created_at", -1).to_list(500)


@api.post("/memories")
async def add_memory(body: MemoryIn, user: dict = Depends(require_user)):
    doc = {"id": str(uuid.uuid4()), "user_id": user["user_id"], "fact": body.fact, "tag": body.tag, "source": "manual", "created_at": now()}
    await db.memories.insert_one(dict(doc))
    doc.pop("user_id")
    return doc


@api.delete("/memories/{mid}")
async def delete_memory(mid: str, user: dict = Depends(require_user)):
    r = await db.memories.delete_one({"id": mid, "user_id": user["user_id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Memória não encontrada")
    return {"ok": True}


@api.get("/settings")
async def get_settings(user: dict = Depends(require_user)):
    st = await db.settings.find_one({"user_id": user["user_id"]}, {"_id": 0, "user_id": 0})
    return st or SettingsIn().model_dump()


@api.put("/settings")
async def put_settings(body: SettingsIn, user: dict = Depends(require_user)):
    await db.settings.update_one({"user_id": user["user_id"]}, {"$set": body.model_dump()}, upsert=True)
    return body.model_dump()


# ---------- Routes: API keys ----------
def hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


@api.get("/keys")
async def list_keys(user: dict = Depends(require_user)):
    return await db.api_keys.find({"user_id": user["user_id"], "revoked": False}, {"_id": 0, "hash": 0, "user_id": 0}).sort("created_at", -1).to_list(100)


@api.post("/keys")
async def create_key(body: KeyIn, user: dict = Depends(require_user)):
    raw = "adam_live_" + secrets.token_urlsafe(32)
    doc = {"id": str(uuid.uuid4()), "user_id": user["user_id"], "name": body.name, "prefix": raw[:16] + "…",
           "hash": hash_key(raw), "revoked": False, "created_at": now(), "last_used_at": None}
    await db.api_keys.insert_one(dict(doc))
    return {"id": doc["id"], "name": doc["name"], "prefix": doc["prefix"], "created_at": doc["created_at"], "key": raw}


@api.delete("/keys/{kid}")
async def revoke_key(kid: str, user: dict = Depends(require_user)):
    r = await db.api_keys.update_one({"id": kid, "user_id": user["user_id"], "revoked": False}, {"$set": {"revoked": True}})
    if not r.matched_count:
        raise HTTPException(404, "Chave não encontrada")
    return {"ok": True}


async def require_api_key(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    raw = request.headers.get("x-api-key") or (auth[7:] if auth.lower().startswith("bearer ") else "")
    if not raw.startswith("adam_"):
        raise HTTPException(401, "Chave de API ausente ou inválida")
    rec = await db.api_keys.find_one({"hash": hash_key(raw), "revoked": False}, {"_id": 0})
    if not rec:
        raise HTTPException(401, "Chave de API inválida ou revogada")
    await db.api_keys.update_one({"id": rec["id"]}, {"$set": {"last_used_at": now()}})
    return {"user_id": rec["user_id"]}


@api.post("/v1/chat")
async def v1_chat(body: V1ChatIn, key: dict = Depends(require_api_key)):
    parts, files, cid, err = [], [], None, None
    async for ev in chat_turn(key["user_id"], ChatIn(message=body.message, conversation_id=body.conversation_id, engine=body.engine)):
        if ev["type"] == "delta":
            parts.append(ev["content"])
        elif ev["type"] == "file":
            files.append(ev["file"])
        elif ev["type"] in ("meta", "done"):
            cid = ev["conversation_id"]
        elif ev["type"] == "error":
            err = ev["message"]
    if err:
        raise HTTPException(404, err)
    return {"conversation_id": cid, "engine": body.engine, "reply": "".join(parts).strip(), "files": files}


@api.get("/v1/files/{fid}")
async def v1_file(fid: str, key: dict = Depends(require_api_key)):
    return await download_file(fid, key)


# ---------- Routes: neural lab ----------
CORPUS_BASE = (ROOT_DIR / "neural_corpus.txt").read_text(encoding="utf-8")
NEURAL_ID = "adam-nano-v1"


async def corpus_text() -> str:
    extra = await db.neural_corpus.find({}, {"_id": 0, "text": 1}).to_list(500)
    return CORPUS_BASE + "\n" + "\n".join(e["text"] for e in extra)


async def train_and_save(steps: int):
    text = await corpus_text()
    await asyncio.to_thread(neural.train, text, steps)
    blob = await asyncio.to_thread(neural.export)
    await db.neural_models.update_one({"id": NEURAL_ID}, {"$set": {"state": Binary(blob), "step": neural.state["step"],
                                                                     "losses": neural.state["losses"][-2000:], "updated_at": now()}}, upsert=True)


@api.get("/neural/status")
async def neural_status(user: dict = Depends(require_user)):
    extra = await db.neural_corpus.count_documents({})
    return {**neural.info(), "corpus_chars": len(await corpus_text()), "corpus_contributions": extra}


@api.post("/neural/train")
async def neural_train(body: TrainIn, user: dict = Depends(require_user)):
    if neural.state["training"]:
        raise HTTPException(409, "Treinamento já em andamento")
    neural.state["training"] = True
    app.state.train_task = asyncio.create_task(train_and_save(body.steps))
    return {"ok": True, "steps": body.steps}


@api.post("/neural/generate")
async def neural_generate(body: GenerateIn, user: dict = Depends(require_user)):
    text = await asyncio.to_thread(neural.generate, body.prompt, body.max_tokens, body.temperature)
    return {"prompt": body.prompt, "completion": text}


@api.post("/neural/corpus")
async def neural_corpus_add(body: CorpusIn, user: dict = Depends(require_user)):
    await db.neural_corpus.insert_one({"id": str(uuid.uuid4()), "user_id": user["user_id"], "text": body.text, "created_at": now()})
    return {"ok": True, "chars": len(body.text)}


app.include_router(api)
app.include_router(clerk_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    app.state.index_task = asyncio.create_task(ensure_indexes())
    try:
        await asyncio.to_thread(storage.init_storage)
    except Exception as e:
        logger.error("Storage init failed: %s", e)
    doc = await db.neural_models.find_one({"id": NEURAL_ID})
    if doc:
        try:
            neural.load(bytes(doc["state"]), doc.get("step", 0), doc.get("losses", []))
            logger.info("AdamNano carregado (step %s)", doc.get("step"))
        except Exception as e:
            logger.error("Falha ao carregar AdamNano: %s", e)


@app.on_event("shutdown")
async def shutdown():
    app.state.index_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.index_task
    client.close()
