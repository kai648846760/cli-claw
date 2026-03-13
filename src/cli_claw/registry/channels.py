from __future__ import annotations

DEFAULT_CHANNEL_CLASSES = {
    "feishu": "cli_claw.channels.feishu.FeishuChannel",
    "telegram": "cli_claw.channels.telegram.TelegramChannel",
    "slack": "cli_claw.channels.slack.SlackChannel",
    "discord": "cli_claw.channels.discord.DiscordChannel",
    "dingtalk": "cli_claw.channels.dingtalk.DingTalkChannel",
    "qq": "cli_claw.channels.qq.QQChannel",
    "whatsapp": "cli_claw.channels.whatsapp.WhatsAppChannel",
    "email": "cli_claw.channels.email.EmailChannel",
    "mochat": "cli_claw.channels.mochat.MochatChannel",
    "local": "cli_claw.channels.local.LocalChannel",
}
