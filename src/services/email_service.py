"""
EmailService — envio transacional via Resend.

Por que isolado num módulo próprio: trocar de provedor (Resend → SES,
SendGrid, etc.) é mexer SÓ aqui. O resto do código chama `send_email(...)`
e não sabe quem manda.

Modo de falha: tudo é best-effort. Se a key não estiver setada, ou se o
Resend recusar, NÃO levantamos exceção pro caller — logamos e devolvemos
False. Razão: o endpoint de "esqueci senha" deve responder igual quer o
email exista ou não (anti-enumeração); falhas de envio não devem virar
sinal pra o atacante.
"""
from __future__ import annotations

import logging
from typing import Optional

from src.config import FRONTEND_URL, RESEND_API_KEY, RESEND_FROM_EMAIL

logger = logging.getLogger(__name__)


def _client():
    """Importa e configura o resend só quando precisa enviar.
    Se a lib não estiver instalada (dev sem o pacote), devolve None."""
    if not RESEND_API_KEY:
        return None
    try:
        import resend
    except ImportError:
        logger.warning("EmailService: lib 'resend' não instalada — email não vai sair.")
        return None
    resend.api_key = RESEND_API_KEY
    return resend


def send_email(*, to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    """Envia um email transacional. Retorna True se enviou, False senão.
    NUNCA levanta exceção — falha silenciosa via log."""
    client = _client()
    if client is None:
        logger.info(
            "EmailService: pulando envio pra %s (key/lib ausente) — assunto %r",
            to, subject,
        )
        return False
    try:
        payload = {
            "from": RESEND_FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text
        client.Emails.send(payload)
        logger.info("EmailService: enviado pra %s — assunto %r", to, subject)
        return True
    except Exception as exc:
        logger.warning("EmailService: envio falhou pra %s: %s", to, exc)
        return False


def send_password_reset_email(*, to: str, name: str, reset_token: str) -> bool:
    """
    Envia o email de "recuperação de senha" com o link que leva pro front:
        {FRONTEND_URL}/reset-password?token=...
    """
    link = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    greeting = name.strip() or "Olá"
    subject = "Recuperação de senha — Football Analytics"
    html = f"""\
<!doctype html>
<html>
  <body style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0a0a0a; color:#e4e4e7; padding:24px;">
    <div style="max-width:520px; margin:0 auto; background:#171717; border:1px solid #27272a; border-radius:12px; padding:32px;">
      <h2 style="color:#fff; margin:0 0 16px;">Recuperação de senha</h2>
      <p style="line-height:1.6; color:#d4d4d8;">{greeting},</p>
      <p style="line-height:1.6; color:#d4d4d8;">
        Recebemos um pedido pra redefinir a senha da sua conta na
        <strong style="color:#22c55e;">Football Analytics</strong>. Clique no
        botão abaixo pra criar uma nova:
      </p>
      <p style="text-align:center; margin:28px 0;">
        <a href="{link}"
           style="background:#fb923c; color:#0a0a0a; text-decoration:none; font-weight:600; padding:12px 24px; border-radius:8px; display:inline-block;">
          Redefinir senha
        </a>
      </p>
      <p style="line-height:1.6; color:#a1a1aa; font-size:13px;">
        Ou copie e cole este endereço no navegador:<br/>
        <span style="color:#71717a; word-break:break-all;">{link}</span>
      </p>
      <hr style="border:none; border-top:1px solid #27272a; margin:24px 0;"/>
      <p style="line-height:1.6; color:#71717a; font-size:12px;">
        Este link expira em 1 hora. Se você não pediu essa recuperação,
        pode ignorar — sua senha continua a mesma.
      </p>
    </div>
  </body>
</html>"""
    text = (
        f"{greeting},\n\n"
        f"Recebemos um pedido pra redefinir a senha da sua conta na Football Analytics.\n"
        f"Acesse o link abaixo pra criar uma nova senha:\n\n"
        f"{link}\n\n"
        f"O link expira em 1 hora. Se você não pediu essa recuperação, pode ignorar."
    )
    return send_email(to=to, subject=subject, html=html, text=text)
