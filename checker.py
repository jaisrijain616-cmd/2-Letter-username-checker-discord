#!/usr/bin/env python3
import asyncio
import json
import os
import random
import string
import sys
import time
from typing import List, Optional, Set

import aiohttp
from colorama import Fore, Style, init

# Initialize colorama for cross-platform colored terminal output
init(autoreset=True)

CONFIG_FILE = "config.json"
PROXY_FILE = "proxy.txt"
LIST_FILE = "list.txt"


class ProxyManager:
    """Manages loading, parsing, rotation, and cooldowns for host:port HTTP proxies."""

    def __init__(self, proxy_file: str, cooldown_seconds: int):
        self.proxy_file = proxy_file
        self.cooldown_seconds = cooldown_seconds
        self.proxies: List[str] = []
        self.cooldowns: dict[str, float] = {}
        self.load_proxies()

    def load_proxies(self) -> None:
        if not os.path.exists(self.proxy_file):
            print(f"{Fore.YELLOW}[!] {self.proxy_file} not found. Running without proxies.")
            return

        with open(self.proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                # Normalize host:port format for aiohttp by prepending http:// if no scheme exists
                if not line.startswith("http://") and not line.startswith("https://") and not line.startswith("socks"):
                    proxy_url = f"http://{line}"
                else:
                    proxy_url = line
                
                self.proxies.append(proxy_url)

        print(f"{Fore.GREEN}[+] Loaded {len(self.proxies)} proxies from {self.proxy_file}.")

    def get_proxy(self) -> Optional[str]:
        if not self.proxies:
            return None

        current_time = time.time()
        # Filter out proxies currently serving cooldown periods
        available_proxies = [
            p for p in self.proxies 
            if current_time >= self.cooldowns.get(p, 0)
        ]

        if not available_proxies:
            # Fallback to the proxy whose cooldown expires the soonest
            earliest_proxy = min(self.proxies, key=lambda p: self.cooldowns.get(p, 0))
            wait_time = self.cooldowns.get(earliest_proxy, 0) - current_time
            if wait_time > 0:
                print(f"{Fore.YELLOW}[!] All proxies in cooldown. Next available in {wait_time:.1f}s...")
            return earliest_proxy

        return random.choice(available_proxies)

    def mark_failed(self, proxy: str) -> None:
        if proxy:
            self.cooldowns[proxy] = time.time() + self.cooldown_seconds
            print(f"{Fore.RED}[!] Proxy placed on cooldown for {self.cooldown_seconds}s: {proxy}")


class DiscordChecker:
    """Handles asynchronous availability checks using Discord's unauthenticated endpoint and webhook notifications."""

    def __init__(self, config: dict, proxy_manager: ProxyManager):
        self.config = config
        self.proxy_manager = proxy_manager
        self.webhook_url = config.get("webhook_url")
        self.timeout = aiohttp.ClientTimeout(total=config.get("request_timeout", 15))
        self.max_retries = config.get("max_retries", 3)
        self.semaphore = asyncio.Semaphore(config.get("concurrency", 10))

        # Statistics trackers
        self.checked_count = 0
        self.available_count = 0
        self.taken_count = 0
        self.error_count = 0

    @staticmethod
    def is_valid_format(username: str) -> bool:
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

    async def send_webhook(self, session: aiohttp.ClientSession, username: str) -> None:
        if not self.webhook_url or "YOUR_WEBHOOK" in self.webhook_url:
            return
        
        payload = {
            "content": f"🎉 **Available Discord Username Found!**\n`{username}`"
        }
        try:
            async with session.post(self.webhook_url, json=payload, timeout=10) as resp:
                if resp.status not in (200, 204):
                    print(f"{Fore.YELLOW}[!] Failed to send webhook alert for {username} (Status: {resp.status})")
        except Exception as e:
            print(f"{Fore.YELLOW}[!] Webhook error for {username}: {e}")

    async def check_username(self, session: aiohttp.ClientSession, username: str) -> None:
        async with self.semaphore:
            if not self.is_valid_format(username):
                return

            # Discord official sign-up unauthenticated endpoint
            url = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
            payload = {"username": username}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Accept-Language": "en-US,en;q=0.9"
            }

            for attempt in range(self.max_retries):
                proxy = self.proxy_manager.get_proxy()
                try:
                    async with session.post(
                        url, 
                        json=payload, 
                        headers=headers,
                        proxy=proxy, 
                        timeout=self.timeout
                    ) as resp:
                        self.checked_count += 1
                        
                        if resp.status == 200:
                            data = await resp.json()
                            # Discord returns {"taken": false} if available, {"taken": true} if taken
                            taken = data.get("taken", True)
                            if not taken:
                                self.available_count += 1
                                print(f"{Fore.GREEN}[AVAILABLE] {username}")
                                asyncio.create_task(self.send_webhook(session, username))
                            else:
                                self.taken_count += 1
                                print(f"{Fore.RED}[TAKEN] {username}")
                            return

                        elif resp.status == 429:
                            # Rate limited
                            print(f"{Fore.YELLOW}[!] Rate limited on {username} using proxy {proxy}")
                            if proxy:
                                self.proxy_manager.mark_failed(proxy)
                            await asyncio.sleep(2 * (attempt + 1))
                            continue

                        elif resp.status in (400, 403):
                            # Client-side validation rejection or proxy block
                            self.taken_count += 1
                            return

                        else:
                            print(f"{Fore.YELLOW}[!] Unexpected status {resp.status} for {username}")
                            if proxy:
                                self.proxy_manager.mark_failed(proxy)
                            await asyncio.sleep(1)

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if proxy:
                        self.proxy_manager.mark_failed(proxy)
                    if attempt == self.max_retries - 1:
                        self.error_count += 1
                        print(f"{Fore.RED}[!] Network/Timeout error checking {username}: {type(e).__name__}")
                    await asyncio.sleep(1)

            self.error_count += 1


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "webhook_url": "https://discord.com/api/webhooks/YOUR_WEBHOOK",
            "concurrency": 10,
            "request_timeout": 15,
            "max_retries": 3,
            "proxy_cooldown_seconds": 300
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        print(f"{Fore.GREEN}[+] Created default {CONFIG_FILE}. Please configure your webhook URL.")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    required_keys = ["webhook_url", "concurrency", "request_timeout", "max_retries", "proxy_cooldown_seconds"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required configuration key in {CONFIG_FILE}: '{key}'")

    return config


def generate_usernames(mode: int, count: int) -> List[str]:
    usernames: Set[str] = set()
    
    letters = string.ascii_lowercase
    digits = string.digits
    symbols = "._"
    
    pool_opt2 = letters + digits + symbols
    pool_opt4 = letters + digits + symbols

    while len(usernames) < count:
        if mode == 2:
            # Random 4-character (letters, digits, dots, underscores)
            name = "".join(random.choices(pool_opt2, k=4))
        elif mode == 3:
            # Random 4-letter-only (a-z)
            name = "".join(random.choices(letters, k=4))
        elif mode == 4:
            # Random 3-character (Discord allowed chars)
            name = "".join(random.choices(pool_opt4, k=3))
        else:
            break

        if DiscordChecker.is_valid_format(name):
            usernames.add(name)

    return list(usernames)


async def run_checker(checker: DiscordChecker, usernames: List[str]) -> None:
    print(f"\n{Fore.CYAN}[*] Starting check for {len(usernames)} usernames...\n")
    start_time = time.time()

    connector = aiohttp.TCPConnector(limit=checker.config.get("concurrency", 10))
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [checker.check_username(session, uname) for uname in usernames]
        await asyncio.gather(*tasks)

    duration = time.time() - start_time
    print(f"\n{Fore.GREEN}=== CHECK COMPLETED ===")
    print(f"Total Checked: {checker.checked_count}")
    print(f"Available    : {Fore.GREEN}{checker.available_count}{Style.RESET_ALL}")
    print(f"Taken        : {Fore.RED}{checker.taken_count}{Style.RESET_ALL}")
    print(f"Errors       : {Fore.YELLOW}{checker.error_count}{Style.RESET_ALL}")
    print(f"Time Elapsed : {duration:.2f} seconds\n")


def main_menu() -> None:
    try:
        config = load_config()
    except Exception as e:
        print(f"{Fore.RED}[!] Configuration error: {e}")
        sys.exit(1)

    proxy_manager = ProxyManager(PROXY_FILE, config.get("proxy_cooldown_seconds", 300))
    checker = DiscordChecker(config, proxy_manager)

    while True:
        print(f"\n{Fore.CYAN}=== DISCORD USERNAME CHECKER ===")
        print(f"{Fore.YELLOW}1.{Style.RESET_ALL} Load usernames from {LIST_FILE}")
        print(f"{Fore.YELLOW}2.{Style.RESET_ALL} Generate and check random 4-character usernames")
        print(f"{Fore.YELLOW}3.{Style.RESET_ALL} Generate and check random 4-letter-only usernames")
        print(f"{Fore.YELLOW}4.{Style.RESET_ALL} Generate and check random 3-character usernames")
        print(f"{Fore.YELLOW}5.{Style.RESET_ALL} Exit")

        choice = input(f"\n{Fore.GREEN}Select an option (1-5): {Style.RESET_ALL}").strip()

        if choice == "1":
            if not os.path.exists(LIST_FILE):
                print(f"{Fore.RED}[!] {LIST_FILE} does not exist.")
                continue
            with open(LIST_FILE, "r", encoding="utf-8") as f:
                usernames = list(set([line.strip().lower() for line in f if line.strip()]))
            if not usernames:
                print(f"{Fore.RED}[!] {LIST_FILE} is empty.")
                continue
            asyncio.run(run_checker(checker, usernames))

        elif choice in ("2", "3", "4"):
            try:
                count_input = input(f"{Fore.GREEN}Enter number of usernames to generate: {Style.RESET_ALL}").strip()
                count = int(count_input)
                if count <= 0:
                    raise ValueError()
            except ValueError:
                print(f"{Fore.RED}[!] Please enter a valid positive integer.")
                continue

            mode = int(choice)
            usernames = generate_usernames(mode, count)
            asyncio.run(run_checker(checker, usernames))

        elif choice == "5":
            print(f"{Fore.CYAN}[*] Exiting... Goodbye!")
            sys.exit(0)
        else:
            print(f"{Fore.RED}[!] Invalid choice. Please select between 1 and 5.")


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] Interrupted by user. Exiting...")
        sys.exit(0)