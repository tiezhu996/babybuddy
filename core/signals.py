# -*- coding: utf-8 -*-
from django.dispatch import Signal

#: Sent after a soft-deleted entry has been moved back out of the trash.
#: Providing ``instance`` (the restored row, already committed). Kept in
#: ``core`` so other apps -- webhooks -- can listen without ``core`` having
#: to know about them.
entry_restored = Signal()
