"""Pure account-page security decisions shared with the GTK frontend."""

from enum import Enum


class AccountNextAction(str, Enum):
    CONTINUE = "continue"
    BLOCK_LOCKOUT = "block-lockout"
    CONFIRM_PASSWORDLESS_SUDO = "confirm-passwordless-sudo"
    CONFIRM_SUDO = "confirm-sudo"


def account_next_action(
    password: str,
    confirmation: str,
    sudo_without_password: bool,
) -> AccountNextAction:
    """Choose the safe navigation action after field validation succeeds."""
    if password != confirmation:
        raise ValueError("Password fields must match before navigation")
    passwordless = not password
    if passwordless and not sudo_without_password:
        return AccountNextAction.BLOCK_LOCKOUT
    if passwordless:
        return AccountNextAction.CONFIRM_PASSWORDLESS_SUDO
    if sudo_without_password:
        return AccountNextAction.CONFIRM_SUDO
    return AccountNextAction.CONTINUE
