"""Test-only relay wire stand-in; its peer certificate pin is injected."""
from deep32net.pairing import JoinedSide

class StandinRelay(JoinedSide):
    test_only = True
    def __init__(self,*args,injected_pin,**kwargs):
        self.injected_pin = bytes.fromhex(injected_pin)
        super().__init__(*args,**kwargs)
    def peer_pin(self, hello):
        return self.injected_pin
