"""Repositório SQLite de usuários."""

from __future__ import annotations

from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import User
from app.core.ports.repositories import UserRepository


class SqliteUserRepository(UserRepository):

    async def _roles_for(self, db, user_id: str) -> list[str]:
        cur = await db.execute(
            "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = ?",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def get_by_username(self, username: str) -> User | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT id, username, full_name, email, phone, department, title, hashed_password, salt, is_active FROM users WHERE username = ?",
                (username,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            roles = await self._roles_for(db, row[0])
            return User(
                id=UUID(row[0]),
                username=row[1],
                full_name=row[2] or "",
                email=row[3] or "",
                phone=row[4] or "",
                department=row[5] or "",
                title=row[6] or "",
                hashed_password=row[7],
                salt=row[8],
                is_active=bool(row[9]),
                roles=roles,
            )

    async def get_by_id(self, user_id: UUID) -> User | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT id, username, full_name, email, phone, department, title, hashed_password, salt, is_active FROM users WHERE id = ?",
                (str(user_id),),
            )
            row = await cur.fetchone()
            if not row:
                return None
            roles = await self._roles_for(db, row[0])
            return User(
                id=UUID(row[0]),
                username=row[1],
                full_name=row[2] or "",
                email=row[3] or "",
                phone=row[4] or "",
                department=row[5] or "",
                title=row[6] or "",
                hashed_password=row[7],
                salt=row[8],
                is_active=bool(row[9]),
                roles=roles,
            )

    async def create(self, user: User) -> User:
        async with connect() as db:
            await db.execute(
                "INSERT INTO users (id, username, full_name, email, phone, department, title, hashed_password, salt, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(user.id), user.username, user.full_name, user.email, user.phone, user.department, user.title, user.hashed_password, user.salt, int(user.is_active)),
            )
            for role_name in user.roles:
                cur = await db.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
                r = await cur.fetchone()
                if r:
                    await db.execute(
                        "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                        (str(user.id), r[0]),
                    )
            await db.commit()
            return user

    async def list_all(self) -> list[User]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT id, username, full_name, email, phone, department, title, hashed_password, salt, is_active FROM users ORDER BY username"
            )
            rows = await cur.fetchall()
            users: list[User] = []
            for row in rows:
                roles = await self._roles_for(db, row[0])
                users.append(
                    User(
                        id=UUID(row[0]),
                        username=row[1],
                        full_name=row[2] or "",
                        email=row[3] or "",
                        phone=row[4] or "",
                        department=row[5] or "",
                        title=row[6] or "",
                        hashed_password=row[7],
                        salt=row[8],
                        is_active=bool(row[9]),
                        roles=roles,
                    )
                )
            return users

    async def set_active(self, user_id: UUID, active: bool) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE users SET is_active = ? WHERE id = ?",
                (1 if active else 0, str(user_id)),
            )
            await db.commit()

    async def set_roles(self, user_id: UUID, roles: list[str]) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM user_roles WHERE user_id = ?", (str(user_id),))
            for role_name in roles:
                cur = await db.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
                r = await cur.fetchone()
                if r:
                    await db.execute(
                        "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                        (str(user_id), r[0]),
                    )
            await db.commit()

    async def set_password(self, user_id: UUID, hashed: str, salt: str) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE users SET hashed_password = ?, salt = ? WHERE id = ?",
                (hashed, salt, str(user_id)),
            )
            await db.commit()

    async def delete(self, user_id: UUID) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM users WHERE id = ?", (str(user_id),))
            await db.commit()


    async def count_users(self) -> int:
        async with connect() as db:
            cur = await db.execute("SELECT COUNT(*) FROM users")
            row = await cur.fetchone()
            return int(row[0] if row else 0)
