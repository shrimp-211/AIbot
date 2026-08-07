class PermissionTypeFilter:
    def __init__(self, allow_group: bool = True, allow_friend: bool = True, allow_admin: bool = True) -> None:
        self.allow_group = allow_group
        self.allow_friend = allow_friend
        self.allow_admin = allow_admin
