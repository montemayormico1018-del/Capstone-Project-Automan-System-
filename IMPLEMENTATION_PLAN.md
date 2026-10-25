# Automan Car Care System — Implementation Plan

## Executive Summary

After inspecting the existing codebase, the system is **mostly already aligned** with the requested requirements. Most features (auto customer role, internal username generation, role immutability, mechanic availability toggle, manual job orders, configurable pricing, quotation ranges, rejection FAQ, service selection, etc.) are already implemented in the current Flask + Firestore codebase.

The remaining work focuses on **polish, consistency, accessibility, and verification** rather than greenfield development.

---

## Current State Assessment

### Already Implemented ✅

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Customer auto-assigns `customer` role | ✅ | `routes/auth.py:212` — `role='customer'` |
| Internal usernames auto-generated | ✅ | `routes/users.py:20-28` — `generate_internal_username()` |
| Roles cannot be freely changed | ✅ | `routes/users.py:155-157` — blocks role edits |
| Contact number required | ✅ | `routes/auth.py:179-183`, `routes/users.py:78-81` |
| Mechanic availability: 2 states only | ✅ | `models/user.py:22` — `available` / `not_available` |
| Mechanic dashboard toggle | ✅ | `templates/dashboard.html:481-693` |
| Admin/staff can view/change availability | ✅ | `templates/users.html:520`, `routes/users.py:203-218` |
| Only Available mechanics assignable | ✅ | `routes/job_orders.py:201-205` + frontend filter |
| Backend rechecks availability on assignment | ✅ | `routes/job_orders.py:204-205` |
| Job orders manual only (no auto-create) | ✅ | `routes/appointments.py` — no auto JO on approval |
| Admin-defined service pricing | ✅ | `models/service.py:18-19` — `price_small`, `price_large` |
| Automatic service estimate | ✅ | `utils/pricing.py:14-23` |
| Automatic quotation range | ✅ | `routes/appointments.py:629-633` + `SystemSetting.pricing()` |
| Irregular/additional work | ✅ | `routes/job_orders.py:230-254` |
| Predefined rejection reasons | ✅ | `templates/appointments.html:651-708` |
| Service selection (individual + packages) | ✅ | `templates/customer_dashboard.html:2640-2663` |
| Tire service combination validation | ✅ | `routes/appointments.py:23-32` |
| Brake service independent checkboxes | ✅ | `templates/customer_dashboard.html:3454` |
| Chatbot privacy modal | ✅ | `templates/customer_dashboard.html:4687-4693` |
| User management table columns | ✅ | `templates/users.html:411-421` |
| Historical record preservation | ✅ | Soft deletes, no hard deletes on transactional data |

### Gaps / Improvements Needed 🔧

| # | Gap | Priority | Description |
|---|-----|----------|-------------|
| 1 | **Chatbot privacy accessibility** | Medium | Privacy modal is only in `customer_dashboard.html`. The chatbot loads on `index.html` via external script. Need to add accessible privacy notice on `index.html`. |
| 2 | **Service price display by vehicle size** | Medium | Service cards show base `s.price` regardless of vehicle size. Should show size-class-specific price when vehicle is selected. |
| 3 | **Rejection reason consistency** | Low | `rejectAppointment()` and `updateAppointment()` have slightly different reason sets. Unify them. |
| 4 | **Verification & testing** | High | Run through all required test scenarios from section 22 of the prompt. |

---

## Implementation Plan

### Phase 1: Chatbot Privacy Notice (index.html)

**File**: `templates/index.html`

Add a Data Privacy notice section near the chatbot embed or as a footer notice that is accessible from the homepage. The existing modal in `customer_dashboard.html` is good but the chatbot is also loaded on `index.html`.

**Action**: Add a small "Data Privacy" link/button near the chatbot or in the footer that opens the same privacy modal, or add an inline privacy banner near the chatbot script.

### Phase 2: Service Price Display by Vehicle Size Class

**Files**: `templates/customer_dashboard.html`, `routes/appointments.py`

Currently, `renderServices()` shows `s.price` for all services. When a vehicle is selected, the price should reflect the vehicle's size class:
- Small vehicle → `price_small` (or base if unset)
- Midsize → `price` (base)
- Large → `price_large` (or base if unset)

**Actions**:
1. In `routes/appointments.py:get_services()`, include `size_class` in the response when a `vehicle_id` is provided.
2. In `templates/customer_dashboard.html`, update `renderServices()` to show size-class-specific prices.
3. For sub-services, show the appropriate price for the selected vehicle size class.

### Phase 3: Unify Rejection Reasons

**File**: `templates/appointments.html`

Unify the predefined rejection reasons between `rejectAppointment()` (line ~651) and `updateAppointment()` (line ~988).

**Actions**:
1. Create a single shared `REJECTION_REASONS` constant.
2. Update both functions to use the same reasons and default messages.

### Phase 4: Backend Verification

**Files**: Various route files

Ensure all backend validations are in place:
1. Customer role cannot be changed (already blocked)
2. Contact number required for all user creation (already enforced)
3. Mechanic availability is backend source of truth (already enforced)
4. Job orders cannot be auto-created (already enforced)

### Phase 5: Testing

Run through the test scenarios from section 22 of the prompt:
1. Authentication tests
2. Service selection tests
3. Chatbot privacy tests
4. Mechanic availability tests
5. Job order workflow tests
6. Pricing tests

---

## Detailed Change List

### 1. `templates/index.html` — Add Chatbot Privacy Notice

Add near the chatbot script or in the page footer:

```html
<!-- Chatbot Data Privacy Notice -->
<div class="chatbot-privacy-notice" style="...">
  <button data-bs-toggle="modal" data-bs-target="#chatbotPrivacyModal">Data Privacy</button>
  <!-- Same modal as customer_dashboard.html -->
</div>
```

Or add a dedicated section above the chatbot.

### 2. `templates/customer_dashboard.html` — Service Prices by Size Class

In `renderServices()` and `selectSubService()`:
- When `selectedVehicle` is set, look up the vehicle's `size_class`
- Display `price_for(size_class)` instead of `s.price`
- For sub-services, display the sub-service's price for the selected size class

In `routes/appointments.py:get_services()`:
- When `vehicle_id` is provided, include the vehicle's `size_class` in the response JSON so the frontend can use it.

### 3. `templates/appointments.html` — Unified Rejection Reasons

Create a shared constant and use it in both `rejectAppointment()` and `updateAppointment()`.

### 4. Verification

No code changes needed — verify existing behavior matches requirements.

---

## Risk Assessment

- **Low risk**: Most changes are frontend-only (template/JS updates).
- **Medium risk**: Service price display change must not break existing estimate calculation.
- **Low risk**: Rejection reason unification is purely cosmetic.

---

## Rollback Plan

All changes are additive or template-only. If issues arise:
1. Revert template changes to previous versions.
2. Service price display can fall back to `s.price` if size-class lookup fails.
3. Rejection reasons can revert to separate hardcoded lists.
