from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    upi_id = db.Column(db.String(100), unique=True, nullable=False)
    balance = db.Column(db.Float, default=10000.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_enrolled = db.Column(db.Boolean, default=False)
    
    keystroke_profile = db.relationship('KeystrokeProfile', backref='user', uselist=False, lazy=True)
    risk_events = db.relationship('RiskEvent', backref='user', lazy=True)
    transactions = db.relationship('Transaction', backref='user', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'full_name': self.full_name,
            'upi_id': self.upi_id,
            'balance': self.balance,
            'is_enrolled': self.is_enrolled
        }


class KeystrokeProfile(db.Model):
    __tablename__ = 'keystroke_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    
    avg_dwell_time = db.Column(db.Float, default=0.0)      
    avg_flight_time = db.Column(db.Float, default=0.0)     
    avg_press_interval = db.Column(db.Float, default=0.0)  
    avg_backspace_rate = db.Column(db.Float, default=0.0)  
    avg_typing_speed = db.Column(db.Float, default=0.0)    
    avg_jitter = db.Column(db.Float, default=0.0)          
    
    std_dwell = db.Column(db.Float, default=0.0)
    std_flight = db.Column(db.Float, default=0.0)
    std_speed = db.Column(db.Float, default=0.0)
    
    sample_count = db.Column(db.Integer, default=0)
    raw_samples = db.Column(db.Text, default='[]') 
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_samples(self):
        return json.loads(self.raw_samples or '[]')

    def set_samples(self, samples):
        self.raw_samples = json.dumps(samples[-20:]) 

    def to_dict(self):
        return {
            'avg_dwell_time': self.avg_dwell_time,
            'avg_flight_time': self.avg_flight_time,
            'avg_press_interval': self.avg_press_interval,
            'avg_backspace_rate': self.avg_backspace_rate,
            'avg_typing_speed': self.avg_typing_speed,
            'avg_jitter': self.avg_jitter,
            'sample_count': self.sample_count
        }


class RiskEvent(db.Model):
    __tablename__ = 'risk_events'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)   
    risk_level = db.Column(db.String(20), nullable=False)  
    risk_score = db.Column(db.Float, default=0.0)
    features_snapshot = db.Column(db.Text)                 
    resolution = db.Column(db.String(50))                   
    amount = db.Column(db.Float)
    recipient = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'event_type': self.event_type,
            'risk_level': self.risk_level,
            'risk_score': round(self.risk_score, 3),
            'resolution': self.resolution,
            'amount': self.amount,
            'recipient': self.recipient,
            'created_at': self.created_at.isoformat()
        }


class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_upi = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='PENDING')   
    risk_level = db.Column(db.String(20))
    auth_method = db.Column(db.String(50))                  
    txn_id = db.Column(db.String(100), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'recipient_upi': self.recipient_upi,
            'amount': self.amount,
            'status': self.status,
            'risk_level': self.risk_level,
            'auth_method': self.auth_method,
            'txn_id': self.txn_id,
            'created_at': self.created_at.isoformat()
        }
