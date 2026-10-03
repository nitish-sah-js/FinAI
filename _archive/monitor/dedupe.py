from datetime import datetime, timedelta

class CooldownManager:
    def __init__(self):
        # { cooldown_key: {'expires': datetime, 'tier': int, 'alert_id': str, 'kind': str, 'timestamp': datetime} }
        self.active = {}
        
    def get_window_minutes(self, kind: str) -> int:
        if kind in ('price_z', 'volume_z'): return 30
        if kind == 'news_burst': return 60
        if kind == 'sentiment_shift': return 120
        if kind == 'weather_threshold': return 6 * 60
        if kind == 'agri_stress': return 24 * 60
        return 30
        
    def check(self, kind: str, ticker_or_region: str, tier: int):
        key = f"{kind}:{ticker_or_region}"
        now = datetime.utcnow()
        
        # Cleanup expired
        to_del = [k for k, v in self.active.items() if v['expires'] <= now]
        for k in to_del: del self.active[k]
            
        if key in self.active:
            existing = self.active[key]
            if tier > existing['tier']:
                # Escalation
                self.active[key] = {'expires': now + timedelta(minutes=self.get_window_minutes(kind)), 'tier': tier, 'timestamp': now}
                return 'escalate', existing.get('alert_id')
            return 'update', existing.get('alert_id')
            
        self.active[key] = {'expires': now + timedelta(minutes=self.get_window_minutes(kind)), 'tier': tier, 'timestamp': now}
        return 'new', None
