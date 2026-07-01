"""消息解析测试."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapter.message import MessageChain, MessageSegment


def test_text_segment():
    seg = MessageSegment.text("Hello")
    assert seg.type == "text"
    assert seg.to_cq() == "Hello"


def test_at_segment():
    seg = MessageSegment.at("123456")
    assert seg.to_cq() == "[CQ:at,qq=123456]"


def test_cq_parsing():
    raw = "你好[CQ:at,qq=123456]世界[CQ:image,file=test.jpg]"
    chain = MessageChain.from_cq_string(raw)
    assert len(chain) == 4
    assert chain[0].type == "text"
    assert chain[1].type == "at"
    assert chain[2].type == "text"
    assert chain[3].type == "image"


def test_chain_text_extraction():
    raw = "[CQ:at,qq=123] 你好 [CQ:image,url=http://x.com/a.jpg] 世界"
    chain = MessageChain.from_cq_string(raw)
    text = chain.extract_text()
    assert "你好" in text
    assert "世界" in text


def test_empty():
    chain = MessageChain.from_cq_string("")
    assert len(chain) == 0


if __name__ == "__main__":
    test_text_segment(); print("test_text_segment: OK")
    test_at_segment(); print("test_at_segment: OK")
    test_cq_parsing(); print("test_cq_parsing: OK")
    test_chain_text_extraction(); print("test_chain_text_extraction: OK")
    test_empty(); print("test_empty: OK")
    print("All message tests passed")
