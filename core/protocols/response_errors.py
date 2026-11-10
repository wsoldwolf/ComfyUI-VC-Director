"""Describe malformed model JSON without confusing response and EMD positions."""
from ..errors import Diagnostic


def json_failure(error, performance):
    length_stop = performance.get("finish_reason") == "length"
    incomplete = (error.pos >= len(error.doc.rstrip()) or error.msg.startswith("Unterminated string"))
    position = f"LLM応答のJSON内 {error.lineno}行・{error.colno}列（文字位置{error.pos}）"
    if length_stop:
        budget = performance.get("max_tokens")
        message = "出力トークン上限" + (f"（max_tokens={budget}）" if budget is not None else "")
        message += "に達し、JSON応答が途中で終了しました。"
        code = "GENERATION_TRUNCATED"
    elif incomplete:
        message = "JSON応答が未完了です。生成が途中で終了した可能性があります。"
        code = "JSON_INCOMPLETE"
    else:
        message, code = "LLMが生成したJSONの構文が不正です。", "JSON"
    message += f" {position}。原因: {error.msg}。入力EMDの行番号ではありません。"
    return Diagnostic(code, "/", message), length_stop or incomplete
