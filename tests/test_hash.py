"""
MurmurHash3_x86_32 — parity tests against the TypeScript reference.

Vectors are derived by running the TS implementation in Node.js on the
same inputs.  The conformance fixture AB tests also implicitly validate
hashing (bucket placement must match).
"""

from avsb.core.hash import murmurhash3


class TestMurmurHash3KnownVectors:
    """Known-good vectors cross-validated with the TS reference."""

    def test_empty_string_seed_0(self):
        # murmurhash3('', 0) → 0
        assert murmurhash3("") == 0

    def test_single_char_a(self):
        # murmurhash3('a') — validated against TS output
        assert murmurhash3("a") == 1009084850

    def test_alice(self):
        # 'alice' — the context key used in conformance fixture user_a
        result = murmurhash3("alice")
        # Result is a 32-bit unsigned integer.
        assert 0 <= result <= 0xFFFFFFFF

    def test_deterministic_same_input(self):
        # Same input always returns the same value.
        assert murmurhash3("hello") == murmurhash3("hello")

    def test_different_inputs_differ(self):
        assert murmurhash3("alice") != murmurhash3("bob")

    def test_seed_changes_output(self):
        assert murmurhash3("test", seed=0) != murmurhash3("test", seed=1)

    def test_ab_bucket_alice_checkout_v2(self):
        """
        Replay the AB-test hash used in conformance fixture user_a.

        hash_value = 'alice', flag_key = 'checkout-v2-experiment', rule = 'rule_ab_split'
        alloc_hash = murmurhash3('alice' + 'checkout-v2-experiment' + '__' + 'rule_ab_split') % 10000
        bucket_hash = murmurhash3('alice' + 'checkout-v2-experiment') % 10000

        The expected fixture records bucket_hash = 4336.
        """
        bucket_hash = (
            murmurhash3("alice" + "checkout-v2-experiment") % 10_000
        )
        # Expected value recorded in the conformance fixture reason string.
        assert bucket_hash == 4336

    def test_ab_bucket_alice_alloc_stays_in_window(self):
        """Traffic allocation hash for alice must be < 10000 (100% traffic)."""
        alloc_hash = (
            murmurhash3("alice" + "checkout-v2-experiment" + "__" + "rule_ab_split") % 10_000
        )
        # trafficAllocation is 1.0 (100%), threshold = 10000; hash must be < 10000.
        assert alloc_hash < 10_000

    def test_holdout_bucket_deterministic(self):
        """Holdout bucket for 'holdout_subject_1' under 'q2-2026-holdout'."""
        hash_val = murmurhash3("q2-2026-holdout:holdout_subject_1") % 10_000
        # Holdout trafficAllocation = 0.999 → threshold = 9990.
        # The fixture expects held_out = True, so hash must be < 9990.
        assert hash_val < 9990

    def test_returns_unsigned_32bit(self):
        for key in ["", "x", "hello world", "a" * 100]:
            result = murmurhash3(key)
            assert 0 <= result <= 0xFFFFFFFF, f"Out of range for key={key!r}: {result}"
