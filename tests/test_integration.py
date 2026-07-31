from collections import deque

from cliradar.crawler import CrawlLimits, crawl
from cliradar.models import Catalog
from cliradar.session import SwitchSession


class ScriptedChannel:
    def __init__(self) -> None:
        self.buffer: deque[bytes] = deque()
        self.queries: list[str] = []
        self.responses = {
            "?": "  show       Show system information\r\n",
            "show ?": "  version    Software version\r\n",
            "show version ?": "  <cr>       Execute command\r\n",
        }

    def send(self, value: str) -> int:
        if value == "\x15":
            return 1
        self.queries.append(value)
        self.buffer.append(self.responses[value].encode())
        return len(value)

    def recv_ready(self) -> bool:
        return bool(self.buffer)

    def recv(self, size: int) -> bytes:
        value = self.buffer.popleft()
        if len(value) > size:
            self.buffer.appendleft(value[size:])
        return value[:size]


def test_discovers_commands_through_interactive_question_mark() -> None:
    channel = ScriptedChannel()
    session = SwitchSession({"idle_timeout": 0.001, "read_timeout": 0.05})
    session.channel = channel  # type: ignore[assignment]
    catalog = Catalog(device={"identity": "redacted"})

    result = crawl(
        session.query_help,
        catalog,
        seeds=[],
        limits=CrawlLimits(max_depth=4, max_queries=10),
    )

    assert result.queries == 3
    assert result.complete is True
    assert channel.queries == ["?", "show ?", "show version ?"]
    assert catalog.commands["show version"].executable is True
