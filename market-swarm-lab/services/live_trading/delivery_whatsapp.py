"""
WhatsApp alert delivery via Twilio.
"""

import logging
from typing import Optional
from datetime import datetime
import asyncio
from queue import Queue, Empty

from data_types import OrderFlowAlert

logger = logging.getLogger(__name__)


class WhatsAppDelivery:
    """Send alerts via WhatsApp using Twilio."""
    
    def __init__(self, account_sid: str, auth_token: str,
                 from_phone: str, to_phone: str, enabled: bool = True):
        """
        Initialize WhatsApp delivery.
        
        Args:
            account_sid: Twilio account SID
            auth_token: Twilio auth token
            from_phone: Twilio phone number (with country code)
            to_phone: Recipient phone number (with country code)
            enabled: Whether delivery is enabled
        """
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_phone = from_phone
        self.to_phone = to_phone
        self.enabled = enabled
        
        self.sent_count = 0
        self.failed_count = 0
        self.queue = Queue()
        
        # Try to import twilio
        try:
            from twilio.rest import Client
            self.client = Client(account_sid, auth_token) if enabled else None
        except ImportError:
            logger.warning("Twilio not installed, WhatsApp delivery will be simulated")
            self.client = None
    
    async def send_alert(self, alert: OrderFlowAlert) -> bool:
        """
        Send alert via WhatsApp.
        
        Args:
            alert: Alert to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        
        if not self.enabled:
            logger.debug(f"WhatsApp delivery disabled, alert not sent")
            return False
        
        message_text = alert.format_for_whatsapp()
        
        # Queue for async sending
        self.queue.put((alert, message_text))
        
        # Send in background
        asyncio.create_task(self._send_queued())
        
        return True
    
    async def _send_queued(self):
        """Process queued messages."""
        try:
            alert, message_text = self.queue.get_nowait()
        except Empty:
            return
        
        try:
            if self.client:
                # Real Twilio send
                message = self.client.messages.create(
                    from_=f"whatsapp:{self.from_phone}",
                    to=f"whatsapp:{self.to_phone}",
                    body=message_text
                )
                logger.info(f"WhatsApp sent: {message.sid}")
                self.sent_count += 1
            else:
                # Simulated send
                logger.info(f"[SIMULATED] WhatsApp to {self.to_phone}: {message_text}")
                self.sent_count += 1
            
            # Mark alert as sent
            # Note: In real implementation, update database or state
            
        except Exception as e:
            logger.error(f"Failed to send WhatsApp: {e}")
            self.failed_count += 1
            return False
        
        return True
    
    async def send_batch(self, alerts: list) -> int:
        """
        Send batch of alerts.
        
        Args:
            alerts: List of alerts to send
            
        Returns:
            Number successfully sent
        """
        sent = 0
        for alert in alerts:
            if await self.send_alert(alert):
                sent += 1
            await asyncio.sleep(0.5)  # Rate limiting
        
        return sent
    
    def get_stats(self) -> dict:
        """Get delivery statistics."""
        return {
            "sent": self.sent_count,
            "failed": self.failed_count,
            "queued": self.queue.qsize(),
            "from_phone": self.from_phone,
            "to_phone": self.to_phone,
        }
