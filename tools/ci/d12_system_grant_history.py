"""Immutable recent spent campaign identities; legacy grant aliases stay public.

Keep these records separate so new campaigns do not expand the closed grant
validator beyond its existing clean file-length limit. No record grants reuse.
"""

SPENT_CI153_NONCE = "c3f283c54e08074beb44f7dc3a6a2034"
SPENT_CI153_RESERVATION_SHA256 = "30ad104622bb0e4e1f2b8c2682fb9cbf62840ce7b02fdf9140c9f6d9f91ebe04"
SPENT_CI154_NONCE = "3229f8ad759c7d5957fd75f185ab3027"
SPENT_CI154_RESERVATION_SHA256 = "e018077fd3eb69d0eedcd5ed696737a415a08cbf0ae1c3f35c825eef2dc1d5e3"
SPENT_CI155_NONCE = "9b8a2a94c4806d734cd3111e396bd086"
SPENT_CI155_RESERVATION_SHA256 = "c181dd878d066034f23a1cec3405dbd5c2aad80eb2fa06ddc3d15371b5520167"
SPENT_CI156_NONCE = "f6cc6427f2d851c6bf61b36797bd7d29"
SPENT_CI156_RESERVATION_SHA256 = "c3505c556c79890e362087223357c7707eb3eeadbdd88de721c7b1a01f3c500b"
