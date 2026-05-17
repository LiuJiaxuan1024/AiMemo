import tiktoken


_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def encode_text(text: str) -> list[int]:
    return _ENCODING.encode(text)


def decode_tokens(tokens: list[int]) -> str:
    return _ENCODING.decode(tokens)
