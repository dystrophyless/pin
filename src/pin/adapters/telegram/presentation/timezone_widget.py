"""A self-contained Telegram HTML Widget for reading the device's time zone."""

import json


def render_timezone_widget(language: str, token: str) -> str:
    if language == "ru":
        labels = {
            "use": "Использовать пояс устройства",
            "unavailable": "Пояс устройства недоступен. Выберите его в /settings.",
            "sending": "Отправляем…",
            "queued": "Пояс отправлен. Продолжите в чате.",
            "failed": "Не удалось отправить пояс. Попробуйте ещё раз или откройте /settings.",
        }
    else:
        labels = {
            "use": "Use device time zone",
            "unavailable": "Device time zone unavailable. Choose it in /settings.",
            "sending": "Sending…",
            "queued": "Time zone sent. Continue in the chat.",
            "failed": "Could not send the time zone. Try again or open /settings.",
        }
    return (
        """
<p id="zone"></p><button id="use" hidden></button><p id="status" aria-live="polite"></p>
<script>
const labels = """
        + json.dumps(labels, ensure_ascii=False)
        + """;
const token = """
        + json.dumps(token)
        + """;
const button = document.getElementById("use");
const status = document.getElementById("status");
const widget = window.TelegramWidget;
let zone;
try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
} catch {}
const data = token + "|" + zone;
if (widget && zone && (zone === "UTC" || zone.includes("/"))
        && new TextEncoder().encode(data).length <= 64) {
    document.getElementById("zone").textContent = zone;
    button.textContent = labels.use;
    button.hidden = false;
    button.addEventListener("click", async () => {
        if (button.disabled) return;
        button.disabled = true;
        status.textContent = labels.sending;
        try {
            const answer = await widget.request(data);
            if (JSON.parse(answer.result).status !== "queued") throw new Error();
            status.textContent = labels.queued;
        } catch {
            status.textContent = labels.failed;
            button.disabled = false;
        }
    });
} else {
    status.textContent = labels.unavailable;
}
if (widget) widget.ready();
</script>""".strip()
    )
