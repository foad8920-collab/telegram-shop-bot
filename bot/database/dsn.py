from bot.misc.env import EnvKeys


def dsn() -> str:
    return EnvKeys.DATABASE_URL
