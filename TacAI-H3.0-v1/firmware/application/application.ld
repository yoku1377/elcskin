ENTRY(Reset_Handler)

_estack = 0x20001FF0;
_Min_Heap_Size = 0x200;
_Min_Stack_Size = 0x800;

MEMORY
{
  RAM (xrw)  : ORIGIN = 0x20000000, LENGTH = 0x1FF0
  FLASH (rx) : ORIGIN = 0x08002000, LENGTH = 52K
}

SECTIONS
{
  .isr_vector : { . = ALIGN(4); KEEP(*(.isr_vector)) . = ALIGN(4); } > FLASH
  .text :
  {
    . = ALIGN(4);
    *(.text*) *(.rodata*)
    KEEP(*(.init)) KEEP(*(.fini))
    . = ALIGN(4);
    _etext = .;
  } > FLASH
  .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
  .ARM.exidx : { __exidx_start = .; *(.ARM.exidx*) __exidx_end = .; } > FLASH
  .preinit_array : { PROVIDE_HIDDEN(__preinit_array_start = .); KEEP(*(.preinit_array*)) PROVIDE_HIDDEN(__preinit_array_end = .); } > FLASH
  .init_array : { PROVIDE_HIDDEN(__init_array_start = .); KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array*)) PROVIDE_HIDDEN(__init_array_end = .); } > FLASH
  .fini_array : { PROVIDE_HIDDEN(__fini_array_start = .); KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array*)) PROVIDE_HIDDEN(__fini_array_end = .); } > FLASH
  _sidata = LOADADDR(.data);
  .data :
  {
    . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .;
  } > RAM AT > FLASH
  .bss (NOLOAD) :
  {
    . = ALIGN(4); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .;
  } > RAM
  ._user_heap_stack (NOLOAD) :
  {
    . = ALIGN(8); PROVIDE(end = .); PROVIDE(_end = .);
    . = . + _Min_Heap_Size; . = . + _Min_Stack_Size; . = ALIGN(8);
  } > RAM
  /DISCARD/ : { *(.note*) *(.comment*) }
}

ASSERT(ORIGIN(FLASH) + LENGTH(FLASH) <= 0x0800F000, "Application overlaps IAP metadata")
ASSERT(_estack <= 0x20001FF0, "Application stack overlaps IAP request word")
