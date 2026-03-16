import asyncio
import logging
import pickle
import os
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from steam.client import SteamClient
from steam.guard import SteamAuthenticator, SteamGuardAccount
from steam.enums import EResult

class SteamAccount:
    """Класс для управления одним Steam аккаунтом с поддержкой Steam Guard"""
    
    def __init__(self, name: str, username: str, password: str, games: List[str]):
        self.name = name
        self.username = username
        self.password = password
        self.games = games
        self.client = SteamClient()
        self.is_running = False
        self.logged_in = False
        self.current_games = []
        self.authenticator = None
        self.awaiting_2fa = False
        self.login_future = None
        self.steam_id = None
        
        # Настройка callback'ов для SteamClient
        self.client.on('connected', self._on_connected)
        self.client.on('logged_on', self._on_logged_on)
        self.client.on('login_key', self._on_login_key)
        self.client.on('error', self._on_error)
        self.client.on('disconnected', self._on_disconnected)
        
        # Загрузка сохраненной сессии если есть
        self._load_session()
        
    def _on_connected(self):
        """Callback при подключении к Steam"""
        logging.info(f"[{self.name}] Подключен к Steam")
        
    def _on_logged_on(self):
        """Callback при успешном входе"""
        self.logged_in = True
        self.awaiting_2fa = False
        self.steam_id = self.client.steam_id
        logging.info(f"[{self.name}] Успешный вход в Steam (ID: {self.steam_id})")
        
        # Сохраняем сессию для будущих входов
        self._save_session()
        
        if self.login_future and not self.login_future.done():
            self.login_future.set_result(True)
            
    def _on_login_key(self, login_key):
        """Callback при получении login key"""
        logging.info(f"[{self.name}] Получен login key")
        self._save_session()
        
    def _on_error(self, result):
        """Callback при ошибке"""
        error_msg = f"Ошибка Steam: {result!r}"
        logging.error(f"[{self.name}] {error_msg}")
        
        if result == EResult.InvalidPassword:
            if self.login_future and not self.login_future.done():
                self.login_future.set_exception(Exception("Неверный пароль"))
        elif result == EResult.TwoFactorCodeMismatch:
            if self.login_future and not self.login_future.done():
                self.login_future.set_exception(Exception("Неверный код Steam Guard"))
                
    def _on_disconnected(self):
        """Callback при отключении"""
        logging.info(f"[{self.name}] Отключен от Steam")
        self.logged_in = False
        
    def _get_session_path(self) -> Path:
        """Получить путь к файлу сессии"""
        session_dir = Path("sessions")
        session_dir.mkdir(exist_ok=True)
        return session_dir / f"{self.username}.session"
        
    def _save_session(self):
        """Сохранить сессию в файл"""
        try:
            session_path = self._get_session_path()
            with open(session_path, 'wb') as f:
                pickle.dump({
                    'username': self.username,
                    'steam_id': self.steam_id,
                    'login_key': self.client.login_key if hasattr(self.client, 'login_key') else None
                }, f)
            logging.info(f"[{self.name}] Сессия сохранена")
        except Exception as e:
            logging.error(f"[{self.name}] Ошибка сохранения сессии: {e}")
            
    def _load_session(self) -> bool:
        """Загрузить сессию из файла"""
        try:
            session_path = self._get_session_path()
            if session_path.exists():
                with open(session_path, 'rb') as f:
                    session = pickle.load(f)
                logging.info(f"[{self.name}] Сессия загружена")
                return True
        except Exception as e:
            logging.error(f"[{self.name}] Ошибка загрузки сессии: {e}")
        return False
        
    async def login(self, two_factor_code: Optional[str] = None) -> Dict[str, Any]:
        """
        Вход в Steam аккаунт
        Возвращает словарь с результатом и, если требуется, запросом 2FA кода
        """
        # Если уже вошли, возвращаем успех
        if self.logged_in:
            return {"success": True, "message": "Уже в сети"}
            
        # Создаем future для ожидания результата
        self.login_future = asyncio.Future()
        
        try:
            # Пытаемся войти
            if two_factor_code:
                # Вход с 2FA кодом
                self.client.login(
                    username=self.username,
                    password=self.password,
                    two_factor_code=two_factor_code
                )
            else:
                # Пробуем войти с сохраненным login key если есть
                self.client.login(
                    username=self.username,
                    password=self.password
                )
            
            # Ждем результата с таймаутом
            try:
                result = await asyncio.wait_for(self.login_future, timeout=30)
                return {"success": True, "message": "Вход выполнен"}
            except asyncio.TimeoutError:
                # Проверяем, требуется ли 2FA
                if self.client.relogin_available and self.client.awaiting_2fa:
                    self.awaiting_2fa = True
                    return {
                        "success": False,
                        "requires_2fa": True,
                        "message": "Требуется код Steam Guard",
                        "account_name": self.name
                    }
                else:
                    return {"success": False, "message": "Таймаут входа"}
                    
        except Exception as e:
            logging.error(f"[{self.name}] Ошибка входа: {e}")
            return {"success": False, "message": str(e)}
        finally:
            self.login_future = None
            
    async def start_boosting(self) -> Dict[str, Any]:
        """Запуск накрутки часов"""
        # Сначала проверяем/обновляем логин
        login_result = await self.login()
        
        if not login_result["success"]:
            if login_result.get("requires_2fa"):
                return login_result  # Возвращаем запрос 2FA
            else:
                return {"success": False, "message": login_result["message"]}
        
        # Если уже запущено
        if self.is_running:
            return {"success": True, "message": "Уже работает"}
            
        # Запускаем накрутку
        self.is_running = True
        self.current_games = self.games
        logging.info(f"[{self.name}] Запуск накрутки для игр {self.games}")
        
        # Здесь должна быть реальная логика накрутки часов
        # В демо-версии просто имитируем
        
        return {"success": True, "message": "Накрутка запущена"}
    
    async def stop_boosting(self) -> Dict[str, Any]:
        """Остановка накрутки часов"""
        if self.is_running:
            self.is_running = False
            self.current_games = []
            logging.info(f"[{self.name}] Накрутка остановлена")
            return {"success": True, "message": "Накрутка остановлена"}
        return {"success": True, "message": "Уже остановлено"}
    
    async def get_stats(self) -> Dict:
        """Получение статистики аккаунта"""
        return {
            "name": self.name,
            "username": self.username,
            "is_running": self.is_running,
            "games": self.current_games if self.is_running else [],
            "logged_in": self.logged_in,
            "steam_id": str(self.steam_id) if self.steam_id else None,
            "awaiting_2fa": self.awaiting_2fa
        }

class SteamManager:
    """Менеджер для управления несколькими Steam аккаунтами"""
    
    def __init__(self):
        self.accounts: Dict[str, SteamAccount] = {}
        self.pending_2fa: Dict[str, asyncio.Future] = {}
        
    def add_account(self, name: str, username: str, password: str, games: List[str]):
        """Добавление аккаунта в менеджер"""
        self.accounts[name] = SteamAccount(name, username, password, games)
        
    async def start_account(self, account_name: str, two_factor_code: Optional[str] = None) -> Dict[str, Any]:
        """Запуск накрутки для конкретного аккаунта"""
        if account_name not in self.accounts:
            return {"success": False, "message": f"Аккаунт {account_name} не найден"}
            
        account = self.accounts[account_name]
        
        # Если есть ожидающий 2FA запрос, обрабатываем его
        if two_factor_code and account_name in self.pending_2fa:
            future = self.pending_2fa.pop(account_name)
            if not future.done():
                future.set_result(two_factor_code)
            return {"success": True, "message": "Код отправлен"}
        
        # Запускаем накрутку
        result = await account.start_boosting()
        
        # Если требуется 2FA, создаем future для ожидания кода
        if not result["success"] and result.get("requires_2fa"):
            self.pending_2fa[account_name] = asyncio.Future()
            result["awaiting_code"] = True
            
        return result
    
    async def stop_account(self, account_name: str) -> Dict[str, Any]:
        """Остановка накрутки для конкретного аккаунта"""
        if account_name not in self.accounts:
            return {"success": False, "message": f"Аккаунт {account_name} не найден"}
            
        return await self.accounts[account_name].stop_boosting()
    
    async def get_all_stats(self) -> Dict:
        """Получение статистики всех аккаунтов"""
        stats = {}
        for name, account in self.accounts.items():
            stats[name] = await account.get_stats()
        return stats
    
    async def get_account_stats(self, account_name: str) -> Optional[Dict]:
        """Получение статистики конкретного аккаунта"""
        if account_name in self.accounts:
            return await self.accounts[account_name].get_stats()
        return None
    
    def is_awaiting_2fa(self, account_name: str) -> bool:
        """Проверяет, ожидает ли аккаунт ввода 2FA кода"""
        return account_name in self.pending_2fa
