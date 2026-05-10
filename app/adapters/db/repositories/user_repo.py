"""Repositório PostgreSQL de usuários."""

from __future__ import annotations

from uuid import UUID

from app.adapters.db.postgres import connect
from app.core.domain.entities import User
from app.core.ports.repositories import UserRepository


_USER_SELECT = (
    "SELECT id::text, username, full_name, email, phone, department, title, "
    "hashed_password, salt, is_active FROM users"
)


def _row_to_user(row, roles: list[str]) -> User:
    return User(
        id=UUID(row["id"]),
        username=row["username"],
        full_name=row["full_name"] or "",
        email=row["email"] or "",
        phone=row["phone"] or "",
        department=row["department"] or "",
        title=row["title"] or "",
        hashed_password=row["hashed_password"],
        salt=row["salt"],
        is_active=bool(row["is_active"]),
        roles=roles,
    )


class PgUserRepository(UserRepository):

    async def _roles_for(self, db, user_id: str) -> list[str]:
        rows = await db.fetch(
            "SELECT r.name FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = $1::uuid",
            user_id,
        )
        return [r["name"] for r in rows]

    async def get_by_username(self, username: str) -> User | None:
        async with connect() as db:
            row = await db.fetchrow(f"{_USER_SELECT} WHERE username = $1", username)
            if not row:
                return None
            roles = await self._roles_for(db, row["id"])
            return _row_to_user(row, roles)

    async def get_by_id(self, user_id: UUID) -> User | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_USER_SELECT} WHERE id = $1::uuid", str(user_id)
            )
            if not row:
                return None
            roles = await self._roles_for(db, row["id"])
            return _row_to_user(row, roles)

    async def create(self, user: User) -> User:
        async with connect() as db:
            async with db.transaction():
                await db.execute(
                    "INSERT INTO users (id, username, full_name, email, phone, "
                    "department, title, hashed_password, salt, is_active) "
                    "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                    str(user.id), user.username, user.full_name, user.email,
                    user.phone, user.department, user.title,
                    user.hashed_password, user.salt, user.is_active,
                )
                for role_name in user.roles:
                    role_id = await db.fetchval(
                        "SELECT id FROM roles WHERE name = $1", role_name
                    )
                    if role_id is not None:
                        await db.execute(
                            "INSERT INTO user_roles (user_id, role_id) "
                            "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
                            str(user.id), role_id,
                        )
            return user

    async def list_all(self) -> list[User]:
        async with connect() as db:
            rows = await db.fetch(f"{_USER_SELECT} ORDER BY username")
            users: list[User] = []
            for row in rows:
                roles = await self._roles_for(db, row["id"])
                users.append(_row_to_user(row, roles))
            return users

    async def set_active(self, user_id: UUID, active: bool) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE users SET is_active = $1 WHERE id = $2::uuid",
                active, str(user_id),
            )

    async def set_roles(self, user_id: UUID, roles: list[str]) -> None:
        async with connect() as db:
            async with db.transaction():
                await db.execute(
                    "DELETE FROM user_roles WHERE user_id = $1::uuid", str(user_id)
                )
                for role_name in roles:
                    role_id = await db.fetchval(
                        "SELECT id FROM roles WHERE name = $1", role_name
                    )
                    if role_id is not None:
                        await db.execute(
                            "INSERT INTO user_roles (user_id, role_id) "
                            "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
                            str(user_id), role_id,
                        )

    async def set_profile(
        self,
        user_id: UUID,
        full_name: str,
        email: str,
        phone: str,
        department: str,
        title: str,
    ) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE users SET full_name = $1, email = $2, phone = $3, "
                "department = $4, title = $5 WHERE id = $6::uuid",
                full_name, email, phone, department, title, str(user_id),
            )

    async def set_password(self, user_id: UUID, hashed: str, salt: str) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE users SET hashed_password = $1, salt = $2 "
                "WHERE id = $3::uuid",
                hashed, salt, str(user_id),
            )

    async def delete(self, user_id: UUID) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM users WHERE id = $1::uuid", str(user_id))

    async def count_users(self) -> int:
        async with connect() as db:
            n = await db.fetchval("SELECT COUNT(*) FROM users")
            return int(n or 0)
