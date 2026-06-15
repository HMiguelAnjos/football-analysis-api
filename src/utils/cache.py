import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Process RSS (Linux-only — funciona no container Railway) ──────────────
# /proc/self/status é um arquivo virtual do kernel que expõe VmRSS (Resident
# Set Size, RAM real do processo). Não precisa de dependência externa.
# Retorna None em Windows/Mac (cache.py também é usado em testes locais).

def _process_rss_mb() -> Optional[int]:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Formato: "VmRSS:	  123456 kB"
                    kb = int(line.split()[1])
                    return kb // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class SimpleCache:
    """
    In-memory key-value cache com TTL por entrada.

    API pública
    -----------
    set(key, value, ttl)  – grava / atualiza
    get(key)              – retorna valor ou None se expirado/ausente
    has(key)              – True se a chave existe e não expirou
    invalidate(key)       – remove manualmente
    clear()               – limpa tudo
    count_prefix(prefix)  – conta entradas válidas com determinado prefixo
    status()              – diagnóstico resumido
    collect_expired()     – varredura manual; também roda automático

    GC (mai/2026)
    -------------
    Sem GC ativo, entradas expiradas só são removidas QUANDO alguém pede
    a key específica. Entradas que ninguém pede ficam pra sempre — RAM
    cresce em escada conforme novos game_ids/player_ids entram sem que
    os antigos sumam.

    Solução: `_GC_EVERY_OPS` ops (set ou get) disparam uma varredura
    completa. O(N) numa walk simples — barato considerando que roda
    raramente. Cap configurável; testes podem zerar pra desabilitar.
    """

    # Quantas operações até forçar uma varredura. 1000 é conservador:
    # mesmo com tráfego alto roda no máximo poucas vezes por segundo, e
    # remove milhares de keys expiradas de uma vez.
    _GC_EVERY_OPS = 1000

    def __init__(self, name: Optional[str] = None) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._ops_since_gc = 0
        # Identidade no log do GC (mai/2026) — facilita diagnosticar qual
        # cache está acumulando lixo no painel do Railway. Default ao
        # tipo da classe quando não fornecido (várias instâncias do mesmo
        # tipo ficam indistinguíveis no log, mas pelo menos categorizam).
        self._name = name or type(self).__name__

    def get(self, key: str) -> Optional[Any]:
        self._maybe_gc()
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._maybe_gc()
        self._store[key] = (value, time.monotonic() + ttl)

    def has(self, key: str) -> bool:
        """True se a chave existe e ainda não expirou."""
        return self.get(key) is not None

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove todas as entradas."""
        self._store.clear()
        self._ops_since_gc = 0

    def collect_expired(self) -> int:
        """
        Remove todas entradas expiradas e devolve a quantidade removida.
        Chamado automático a cada `_GC_EVERY_OPS` operações; também
        pode ser chamado manualmente (debug/diagnóstico).
        """
        now = time.monotonic()
        expired_keys = [
            k for k, (_, exp) in list(self._store.items()) if exp <= now
        ]
        for k in expired_keys:
            del self._store[k]
        self._ops_since_gc = 0
        return len(expired_keys)

    def _estimate_size_kb(self) -> int:
        """
        Estima o tamanho do cache em KB serializando em JSON.

        Estratégia:
          - Até 100 entradas: serializa tudo (custo aceitável).
          - Acima de 100: sample dos 50 primeiros, calcula média e
            extrapola pelo total. Erro ~5-15% (suficiente pra detectar
            tendência de crescimento, que é o que importa).

        Mede o cache em memória (`_store`). Pra PersistentCache, o
        disco normalmente é equivalente.

        Custo: serializar 50 entradas é trivial (<1ms mesmo com payloads
        complexos). Roda só a cada 1000 ops via _maybe_gc.
        """
        n = len(self._store)
        if n == 0:
            return 0
        try:
            if n <= 100:
                # Serializa só os values (não tuples internos com TTL).
                payload = [v for v, _ in self._store.values()]
                return len(json.dumps(payload, default=str)) // 1024
            # Sample dos 50 primeiros pra cache grande
            sample_values = []
            for i, (v, _) in enumerate(self._store.values()):
                if i >= 50:
                    break
                sample_values.append(v)
            sample_bytes = len(json.dumps(sample_values, default=str))
            avg_per_entry = sample_bytes / len(sample_values)
            return int((avg_per_entry * n) // 1024)
        except (TypeError, ValueError):
            # Cache com objetos não-serializáveis (raro nesta base, mas
            # defensivo) — devolve -1 como sentinel pra "desconhecido".
            return -1

    def _maybe_gc(self) -> None:
        """Dispara GC se ultrapassou o threshold de ops.

        Loga SEMPRE (INFO) o estado do cache — facilita acompanhar
        crescimento no painel de Logs do Railway. Mensagem fica
        estruturada (key=value) pra ser facilmente filtrável: basta
        buscar `cache_stats` pra listar todos os GCs.
        """
        self._ops_since_gc += 1
        if self._ops_since_gc < self._GC_EVERY_OPS:
            return
        removed = self.collect_expired()
        est_kb = self._estimate_size_kb()
        rss_mb = _process_rss_mb()
        # Total ABSOLUTO do processo no fim da linha — facilita correlacionar
        # quando algum cache específico cresce com aumento de RAM global.
        rss_str = f" process_rss_mb={rss_mb}" if rss_mb is not None else ""
        logger.info(
            "cache_stats name=%s entries=%d removed=%d est_kb=%d%s",
            self._name, len(self._store), removed, est_kb, rss_str,
        )

    def count_prefix(self, prefix: str) -> int:
        """Conta entradas válidas cujo nome começa com *prefix*."""
        now = time.monotonic()
        return sum(
            1
            for k, (_, exp) in list(self._store.items())
            if k.startswith(prefix) and exp > now
        )

    def status(self) -> dict:
        now = time.monotonic()
        valid_keys = [k for k, (_, exp) in list(self._store.items()) if exp > now]
        expired_keys = [k for k, (_, exp) in list(self._store.items()) if exp <= now]
        return {
            "total_entries": len(valid_keys),
            "expired_entries": len(expired_keys),
            "ops_since_gc": self._ops_since_gc,
            "keys": valid_keys,
        }


# Alias para retrocompatibilidade
LocalCacheService = SimpleCache


class PersistentCache(SimpleCache):
    """
    SimpleCache com fallback em disco (JSON).

    Ao fazer `set`, grava também em *path* no disco.
    Ao fazer `get` com miss na memória, tenta carregar do disco.

    Isso garante que médias de temporada sobrevivam a restarts do
    container sem precisar chamar stats.nba.com novamente.

    TTL é armazenado como timestamp Unix absoluto no JSON, então
    funciona corretamente entre processos.

    Path:
      - Por padrão lê `CACHE_DIR` (env, default `/tmp`).
      - `/tmp` é efêmero no Railway → cache wipa todo deploy → custa
        ScraperAPI no próximo warm. Apontar pra um volume persistente
        (`CACHE_DIR=/data` + Railway Volume mountado em /data) elimina
        esse desperdício.
    """

    def __init__(self, path: Optional[str] = None, name: Optional[str] = None) -> None:
        if path is None:
            from src.config import CACHE_DIR
            path = os.path.join(CACHE_DIR, "nba_season_cache.json")
        # Nome do log: usa o basename sem extensão (ex: "nba_pbp_period_cache")
        # se o caller não fornecer. Identifica claramente qual arquivo no log
        # do Railway sem expor o path completo.
        if name is None:
            base = os.path.basename(path)
            name = os.path.splitext(base)[0] or "PersistentCache"
        super().__init__(name=name)
        self._path = path
        self._disk: dict[str, tuple[Any, float]] = {}
        # Garante que o diretório existe (importante quando CACHE_DIR aponta
        # pra um volume montado que pode não ter sido inicializado ainda).
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        except OSError as exc:
            logger.warning("PersistentCache: não consegui criar diretório %s: %s",
                           os.path.dirname(self._path), exc)
        self._load_disk()

    def _load_disk(self) -> None:
        if not os.path.exists(self._path):
            logger.info(
                "PersistentCache: arquivo %s não existe (cache vazio — primeiro start ou volume não-persistente).",
                self._path,
            )
            return
        try:
            with open(self._path, "r") as f:
                raw = json.load(f)
            now = time.time()
            valid = {k: (v, exp) for k, (v, exp) in raw.items() if exp > now}
            stale = len(raw) - len(valid)
            self._disk = valid
            logger.info(
                "PersistentCache: carregou %d entradas válidas de %s%s",
                len(self._disk), self._path,
                f" (descartadas {stale} expiradas)" if stale > 0 else "",
            )
            # Promove pra memória todas as entradas válidas (evita 1 read
            # extra do disco no primeiro acesso de cada chave).
            for key, (value, expiry) in self._disk.items():
                remaining = int(expiry - now)
                if remaining > 0:
                    super().set(key, value, remaining)
        except Exception as exc:
            logger.warning("PersistentCache: falha ao carregar %s: %s", self._path, exc)
            self._disk = {}

    def _save_disk(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._disk, f)
        except Exception as exc:
            logger.warning("PersistentCache: falha ao salvar disco: %s", exc)

    def get(self, key: str) -> Optional[Any]:
        # 1. memória
        value = super().get(key)
        if value is not None:
            return value
        # 2. disco
        entry = self._disk.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.time() > expiry:
            del self._disk[key]
            return None
        # promove para memória (TTL restante)
        remaining = int(expiry - time.time())
        if remaining > 0:
            super().set(key, value, remaining)
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        super().set(key, value, ttl)
        self._disk[key] = (value, time.time() + ttl)
        self._save_disk()

    def collect_expired(self) -> int:
        """
        Sobrescreve: além de limpar `_store` (memória, via super),
        limpa também `_disk` (snapshot serializado). Sem essa expansão
        o JSON em /data continuaria acumulando entradas expiradas
        infinitas, ocupando volume + I/O em cada save.

        Devolve a quantidade removida da memória — a do disco é
        independente porque os TTLs usam relógios diferentes
        (monotonic vs unix epoch).
        """
        removed_mem = super().collect_expired()
        # Disco usa time.time() (epoch); memória usa monotonic. Iterações
        # separadas pra não confundir as escalas.
        now_epoch = time.time()
        disk_expired = [
            k for k, (_, exp) in list(self._disk.items()) if exp <= now_epoch
        ]
        if disk_expired:
            for k in disk_expired:
                del self._disk[k]
            self._save_disk()
        return removed_mem
