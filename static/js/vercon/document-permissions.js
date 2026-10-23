// BC edit permissions are independent of the reverse-order cancellation rule.
export const editLocked = doc => doc?.edit_locked ?? doc?.order_locked;
export const editMessage = doc => doc?.edit_message ?? doc?.order_message ?? '';
