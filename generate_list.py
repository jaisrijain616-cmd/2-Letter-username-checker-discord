#!/usr/bin/env python3
import itertools
import string


def is_valid_discord_username(username: str) -> bool:
    """Validates Discord username formatting rules."""
    if not (2 <= len(username) <= 32):
        return False
    # Permitted characters: lowercase letters (a-z), numbers (0-9), periods (.), underscores (_)
    allowed = set(string.ascii_lowercase + string.digits + "._")
    if not all(c in allowed for c in username):
        return False
    # No consecutive periods
    if ".." in username:
        return False
    # Cannot start or end with a period
    if username.startswith(".") or username.endswith("."):
        return False
    return True


def generate_two_letter_usernames() -> list[str]:
    """Generates all mathematically possible and Discord-valid 2-character usernames."""
    chars = string.ascii_lowercase + string.digits + "._"
    valid_usernames = []

    # Generate all 2-character permutations
    for p in itertools.product(chars, repeat=2):
        name = "".join(p)
        if is_valid_discord_username(name):
            valid_usernames.append(name)

    return valid_usernames


def main():
    print("=== Discord 2-Letter Username Generator ===")
    all_valid = generate_two_letter_usernames()
    print(f"[+] Total valid 2-character combinations possible: {len(all_valid)}")

    print("\nOptions:")
    print("1. Save ALL valid 2-letter combinations")
    print("2. Save a specific quantity")
    
    choice = input("\nSelect an option (1-2): ").strip()

    if choice == "1":
        selected = all_valid
    elif choice == "2":
        try:
            count = int(input(f"Enter number of usernames to save (max {len(all_valid)}): "))
            if count <= 0:
                raise ValueError()
            selected = all_valid[:count]
        except ValueError:
            print("[!] Invalid quantity. Saving all valid combinations by default.")
            selected = all_valid
    else:
        print("[!] Invalid choice. Exiting.")
        return

    # Write to list.txt
    filename = "list.txt"
    with open(filename, "w", encoding="utf-8") as f:
        for username in selected:
            f.write(f"{username}\n")

    print(f"\n[+] Successfully generated and saved {len(selected)} usernames to '{filename}'!")


if __name__ == "__main__":
    main()