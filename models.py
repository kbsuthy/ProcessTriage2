from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(120), default='')
    last_name: Mapped[str] = mapped_column(String(120), default='')
    name: Mapped[str] = mapped_column(String(240), default='')
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), default='')
    created: Mapped[str] = mapped_column(String(32), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    assessments: Mapped[list['Assessment']] = relationship(back_populates='owner')
    feedback_messages: Mapped[list['Feedback']] = relationship(back_populates='author')


class Assessment(Base):
    __tablename__ = 'assessments'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('users.id'), index=True, nullable=True)
    user_email: Mapped[str] = mapped_column(String(320), index=True, default='')
    path: Mapped[str] = mapped_column(String(24), default='')
    status: Mapped[str] = mapped_column(String(24), default='partial')
    name: Mapped[str] = mapped_column(String(255), default='')
    purpose: Mapped[str] = mapped_column(Text, default='')
    process_type: Mapped[str] = mapped_column(String(16), default='')
    deep_dive_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[str] = mapped_column(String(32), default='')
    updated: Mapped[str] = mapped_column(String(32), default='')
    payload: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    owner: Mapped[User | None] = relationship(back_populates='assessments')


class Feedback(Base):
    __tablename__ = 'feedback'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('users.id'), index=True, nullable=True)
    user_email: Mapped[str] = mapped_column(String(320), index=True, default='')
    user_name: Mapped[str] = mapped_column(String(240), default='')
    feedback_type: Mapped[str] = mapped_column(String(24), default='idea')
    message: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    author: Mapped[User | None] = relationship(back_populates='feedback_messages')
