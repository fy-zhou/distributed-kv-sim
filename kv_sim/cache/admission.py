"""Cache admission policy for completed requests."""

def admitted_blocks(request_hashes, remote_blocks, policy):
    if policy == "always":
        return request_hashes
    if policy == "no_remote":
        # Never retain an orphan suffix whose remote prefix was not admitted.
        return request_hashes if remote_blocks == 0 else ()
    raise ValueError("unknown cache admission policy: " + str(policy))
