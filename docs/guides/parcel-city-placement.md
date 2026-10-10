# Parcel-delivery city placement

The detailed city covers local ENU E/N approximately −540…540 m. The current parcel grid is within that extent. Its site altitude is uniformly 50 m; this represents the authored template, not a surveyed landing height.

The second vertiport/pad at `(240, -220, 50)` falls inside the committed lite footprint and detailed mesh bounds of a 75 m building. `customer-6` and `customer-8` also intersect conservative bounds of buildings taller than their site altitude.

Proposed common translation for the template author to review: **ΔENU = (0, -70, 0) m**. Apply it consistently to site positions, fleet seeds, route/geofence geometry and other spatial configuration; task references by id need no rename.

- Vertiport/pad 1 → `(-240, -290, 50)`.

- Vertiport/pad 2 → `(240, -290, 50)`.

This proposal uses conservative GLB POSITION accessor bounds, not collision certification. Both pads are clear of those bounds with a 3 m horizontal margin, and no translated site intersects a building taller than its 50 m altitude with a 2 m margin. Several sites still sit above shorter buildings; use actual roof height or relocate them to open space before claiming landing safety. The lite bundle covers a smaller central excerpt, so some translated sites remain outside its displayed buildings.

No template coordinates were changed. The city reference only selects geometry and preserves runtime state.
