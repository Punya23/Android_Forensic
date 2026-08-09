# Forensic Modules Documentation Index

Quick navigation guide for all forensic modules documentation.

---

## 📚 Documentation Files

### 1. **FORENSIC_MODULES_SETUP.md** ⭐ START HERE
**Location**: `/Users/lakshsorathiya/Android_Forensic/FORENSIC_MODULES_SETUP.md`

Complete setup and installation guide including:
- Prerequisites and system requirements
- Step-by-step installation instructions
- Quick start examples for all 5 modules
- Testing guide
- Integration guide for existing pipeline
- API reference
- Troubleshooting
- Database schema additions

**👉 This is your primary guide for getting started.**

---

### 2. **FORENSIC_MODULES_SUMMARY.md**
**Location**: `/Users/lakshsorathiya/Android_Forensic/FORENSIC_MODULES_SUMMARY.md`

High-level implementation summary with:
- Completion status checklist
- What was implemented for each module
- Test results (19/19 passing)
- Design principles
- Files modified/created
- Quick verification commands

**👉 Use this for a quick overview of what was delivered.**

---

### 3. **FORENSIC_MODULES_README.md**
**Location**: `/Users/lakshsorathiya/Android_Forensic/engine/triage/parsers/FORENSIC_MODULES_README.md`

Detailed technical documentation including:
- Module purpose and forensic justification
- Complete API documentation
- Dataclass specifications
- Usage examples with code
- Design principles explained
- Caveat explanations
- Integration notes

**👉 Use this as the technical reference when implementing.**

---

### 4. **test_forensic_modules.py**
**Location**: `/Users/lakshsorathiya/Android_Forensic/engine/tests/test_forensic_modules.py`

Comprehensive unit test suite with:
- 19 tests covering all 5 modules
- Synthetic test fixtures
- Example usage patterns
- Edge case handling

**👉 Use these tests as working examples.**

---

### 5. **verify_modules.py**
**Location**: `/Users/lakshsorathiya/Android_Forensic/engine/verify_modules.py`

Quick verification script that:
- Tests all 5 modules with synthetic data
- Verifies key features work correctly
- Runs in seconds without a device

**👉 Run this to quickly verify installation.**

```bash
cd /Users/lakshsorathiya/Android_Forensic/engine
python verify_modules.py
```

---

## 🔧 Module Files

### MODULE 1: Bluetooth Correlation
**File**: `engine/triage/parsers/bt_config.py`
- Function: `correlate_bluetooth(bond_store_dict, dumpsys_list)`
- **ENHANCED** existing file with new function

### MODULE 2: Wi-Fi Passwords  
**File**: `engine/triage/parsers/wifi.py`
- Functions: `parse_wifi_config_store_xml()`, `parse_wpa_supplicant_conf()`
- **ENHANCED** existing file with WPA3 support

### MODULE 3: Wi-Fi Traffic History
**File**: `engine/triage/parsers/wifi_live.py`
- Function: `parse_netstats(text)`
- **EXISTING** file - verified compliance with requirements

### MODULE 4: USB Connection State
**File**: `engine/triage/acquire/real.py`
- Function: `get_usb_state(adb)`
- **ENHANCED** existing file with new function

### MODULE 5: Hotspot Indicators
**File**: `engine/triage/parsers/hotspot.py`
- Function: `analyze_hotspot_indicators(wifi_dumpsys, netstats, wifi_config)`
- **NEW** file created

---

## 🚀 Quick Start

### Installation
```bash
cd /Users/lakshsorathiya/Android_Forensic/engine
python -m pytest tests/test_forensic_modules.py -v
```

### Verification
```bash
python verify_modules.py
```

Expected output:
```
======================================================================
ALL MODULES VERIFIED SUCCESSFULLY ✓
======================================================================
```

---

## 📖 Reading Order

**For First-Time Users:**
1. Read `FORENSIC_MODULES_SUMMARY.md` for overview
2. Read `FORENSIC_MODULES_SETUP.md` for installation
3. Run `verify_modules.py` to test
4. Review `FORENSIC_MODULES_README.md` for API details

**For Integration:**
1. Read "Integration Guide" in `FORENSIC_MODULES_SETUP.md`
2. Review test examples in `test_forensic_modules.py`
3. Check API Reference in `FORENSIC_MODULES_README.md`

**For Troubleshooting:**
1. Check "Troubleshooting" section in `FORENSIC_MODULES_SETUP.md`
2. Review test cases for correct usage patterns
3. Check module output `caveats` field

---

## 🎯 Key Features

All 5 modules follow these principles:

✅ **Forensic Honesty** - Never fabricate data  
✅ **Separated Time Semantics** - Different timestamps kept distinct  
✅ **Explicit Caveats** - Every limitation documented  
✅ **Graceful Degradation** - Errors don't stop processing  
✅ **Python 3.10+ Compatible** - Modern type hints  
✅ **No External Dependencies** - Stdlib only  
✅ **Fully Tested** - 19/19 tests passing  

---

## 📊 Test Coverage

| Module | Tests | Status |
|--------|-------|--------|
| MODULE 1: Bluetooth | 3 | ✅ PASS |
| MODULE 2: Wi-Fi Passwords | 4 | ✅ PASS |
| MODULE 3: Wi-Fi Traffic | 3 | ✅ PASS |
| MODULE 4: USB State | 3 | ✅ PASS |
| MODULE 5: Hotspot | 5 | ✅ PASS |
| Dataclass Verification | 1 | ✅ PASS |
| **TOTAL** | **19** | **✅ PASS** |

---

## 🔗 External Links

- **Main Project README**: `README.md` (unchanged, original structure preserved)
- **Project Documentation**: `docs/` directory
- **Engine Source**: `engine/triage/` directory

---

## 📝 Notes

1. **Original README Preserved**: The main `README.md` file was kept unchanged as requested
2. **No External Dependencies**: All modules use Python stdlib only
3. **Production Ready**: All tests passing, ready for integration
4. **Forensically Sound**: Each module includes proper caveats and limitations

---

## 🆘 Getting Help

1. **Installation Issues**: See "Troubleshooting" in `FORENSIC_MODULES_SETUP.md`
2. **API Questions**: Check `FORENSIC_MODULES_README.md`
3. **Test Failures**: Review `test_forensic_modules.py` examples
4. **Integration Help**: See "Integration Guide" in `FORENSIC_MODULES_SETUP.md`

---

**Quick Command Reference:**

```bash
# Verify installation
cd /Users/lakshsorathiya/Android_Forensic/engine
python verify_modules.py

# Run all tests
python -m pytest tests/test_forensic_modules.py -v

# Run specific module test
python -m pytest tests/test_forensic_modules.py::TestBluetoothCorrelation -v
```

---

**Version**: 1.0  
**Last Updated**: August 9, 2026  
**Status**: Complete and Verified ✅
