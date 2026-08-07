class CommandGroupFilter:
    def __init__(self, command_group: str | None = None, command_groups: list[str] | None = None) -> None:
        self.command_group = command_group
        self.command_groups = command_groups or []
