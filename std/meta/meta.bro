Layout :: enum {
	auto
	c
}

FieldInfo :: struct {
	name []u8
	type type
	index usize
}

RecordInfo :: struct {
	name ?[]u8
	fields []FieldInfo
	is_tuple bool
	layout Layout
}

EnumInfo :: struct {
	fields []FieldInfo
}

TypeInfo :: union(enum) {
	invalid void
	void void
	anyopaque void
	bool void
	integer void
	float void
	array void
	pointer void
	slice void
	range void
	optional void
	function void
	enum EnumInfo
	record RecordInfo
	union void
	fallible void
	distinct void
}
