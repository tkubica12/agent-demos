from __future__ import annotations

import argparse

from agui_client import AGUIConversation, DEFAULT_AGUI_URL
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static


class AGUITui(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #chat {
        height: 1fr;
        border: round $surface;
        padding: 1;
    }

    #status {
        height: 1;
        color: $text-muted;
    }

    #prompt {
        dock: bottom;
    }
    """
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+n", "new_thread", "New thread"),
        ("ctrl+l", "clear_chat", "Clear"),
    ]

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        thread_id: str | None = None,
    ) -> None:
        super().__init__()
        self.conversation = AGUIConversation(url, headers=headers, thread_id=thread_id)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield RichLog(id="chat", markup=True, wrap=True, highlight=True)
            yield Static(id="status")
        yield Input(placeholder="Message AG-UI agent. /new, /clear, /quit", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Step 07 Memory AG-UI TUI"
        self.sub_title = self.conversation.url
        self._set_status("Ready")
        self.query_one("#prompt", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text == "/quit":
            self.exit()
            return
        if text == "/new":
            self.action_new_thread()
            return
        if text == "/clear":
            self.action_clear_chat()
            return

        self.query_one("#chat", RichLog).write(f"[bold magenta]You[/bold magenta]: {text}")
        self.query_one("#prompt", Input).disabled = True
        self.stream_response(text)

    def action_new_thread(self) -> None:
        self.conversation.reset()
        self.query_one("#chat", RichLog).write("[dim]New AG-UI thread started.[/dim]")
        self._set_status("New thread")

    def action_clear_chat(self) -> None:
        self.query_one("#chat", RichLog).clear()
        self._set_status("Chat cleared")

    @work(thread=True)
    def stream_response(self, text: str) -> None:
        assistant = ""
        try:
            for event in self.conversation.run(text):
                event_type = event["type"]
                if event_type == "RUN_STARTED":
                    self.call_from_thread(
                        self._set_status,
                        f"Streaming thread={self.conversation.thread_id} run={self.conversation.last_run_id}",
                    )
                elif event_type == "TEXT_MESSAGE_CONTENT":
                    assistant += event.get("delta", "")
                    self.call_from_thread(self._replace_assistant, assistant)
                elif event_type == "RUN_ERROR":
                    self.call_from_thread(
                        self._set_status,
                        event.get("message", "AG-UI run failed"),
                        True,
                    )
                elif event_type == "RUN_FINISHED":
                    self.call_from_thread(
                        self._set_status,
                        f"Done thread={self.conversation.thread_id}",
                    )
        except Exception as exc:
            self.call_from_thread(self._set_status, str(exc), True)
        finally:
            self.call_from_thread(self._enable_prompt)

    def _replace_assistant(self, text: str) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.clear()
        for message in self.conversation.messages:
            role = message["role"]
            label = "You" if role == "user" else "Agent"
            color = "magenta" if role == "user" else "green"
            chat.write(f"[bold {color}]{label}[/bold {color}]: {message['content']}")
        chat.write(f"[bold green]Agent[/bold green]: {text}")

    def _enable_prompt(self) -> None:
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()

    def _set_status(self, text: str, is_error: bool = False) -> None:
        style = "red" if is_error else "dim"
        self.query_one("#status", Static).update(f"[{style}]{text}[/{style}]")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_AGUI_URL)
    parser.add_argument("--bearer-token")
    parser.add_argument("--thread-id", help="Continue a previous raw conversation thread.")
    args = parser.parse_args()
    headers = {}
    if args.bearer_token:
        headers["Authorization"] = f"Bearer {args.bearer_token}"
    AGUITui(args.url, headers=headers, thread_id=args.thread_id).run()


if __name__ == "__main__":
    main()
